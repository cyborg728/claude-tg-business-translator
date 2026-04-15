# План миграции: v2 → v3

Цель: горизонтально масштабируемый Telegram-бот. В v2 уже есть
примитивы очередей (RabbitMQ, Celery, `worker-tasks`, `worker-delivery`,
`deliver` с rate-limit). v3 доводит дело до конца: отделяет **приём
апдейтов** от **исполнения хэндлеров**, шардирует по `chat_id` для
сохранения порядка и убирает single-replica ограничения, завязанные на
состоянии.

Этот документ — только план, **в этом коммите никакого кода не
меняется**. Каждая фаза ниже — отдельный PR/коммит.

---

## 0. Основные принципы

* **Код хэндлеров не трогаем.** Все v2-хэндлеры продолжают работать;
  меняется шина вокруг них.
* **Каждую фазу можно деплоить независимо.** Никакого big-bang
  cutover.
* **Ничего нового не пишется без тестов.** Следуем философии v2 по
  покрытию — unit + integration, только `pytest`.
* **Никаких новых жёстких зависимостей**, если они не заменяют что-то
  существующее. Остаёмся на RabbitMQ/Celery/Redis/SQLite→Postgres.

---

## 1. Текущее состояние (v2, унаследовано в v3)

```
Telegram ──▶ bot Deployment (1 реплика) ─┬──▶ tasks_queue ─▶ worker-tasks
             [ webhook-сервер (PTB)      │
               + хэндлеры                 └──▶ delivery_queue ─▶ worker-delivery ──▶ Telegram
               + Celery producer ]
```

Боли:
1. Webhook-приёмник + хэндлеры + Celery producer живут в одном процессе.
2. `bot` Deployment зафиксирован на `replicas: 1`.
3. SQLite на RWO PVC — блокирует multi-replica хэндлеры.
4. Нет дедупликации по `update_id`; ретраи от Telegram обрабатываются
   дважды.
5. Rate-limiter в `deliver` — best-effort; не учитывается 429
   `retry_after`, нет DLQ.
6. Нет гарантии порядка per-chat при масштабировании хэндлеров на
   несколько реплик.

---

## 2. Целевое состояние (v3)

```
Telegram ─HTTPS─▶ Ingress
                    │
                    ▼
          webhook-receiver (Deployment, HPA по RPS/CPU, N реплик)
             · валидирует secret_token
             · SETNX update:{id}  (дедуп, TTL 1ч)
             · публикует в "updates" exchange (x-consistent-hash, key=chat_id)
             · отдаёт 200 за <50мс
                    │
                    ▼
          RabbitMQ "updates" exchange  ──fanout по хешу──▶  updates.shard.{0..N-1}
                                                                   │
                     (один потребитель in-flight на шард)          ▼
                                                            worker-tasks
                                                             · восстанавливает Update
                                                             · запускает PTB-хэндлеры
                                                             · публикует в delivery_queue
                    ▼
          delivery_queue ─▶ worker-delivery
                            · глобальный token-bucket (Redis)
                            · per-chat token-bucket (Redis)
                            · учитывает 429 retry_after
                            · DLQ после N ретраев
                    ▼
               Telegram Bot API
```

Состояние:
* **БД** → Postgres (managed или StatefulSet). SQLite остаётся как dev-бэкенд.
* **Redis** → без изменений, переиспользуется под дедуп + бакеты.
* **RabbitMQ** → без изменений, добавляется один exchange + шард-очереди.

---

## 3. Фазированная миграция

Каждая фаза заканчивается зелёным тестовым прогоном и деплоящимся
образом. Поздние фазы можно переставить местами, если приоритеты
сдвинутся.

### Фаза 1 — Идемпотентность и упрочнение rate-limit (safety net) ✅ выполнено

Цель: починить корректность до смены топологии.

Сделано:
* `src/cache/idempotency.py`: async `claim_update` через `SET NX EX`, TTL
  настраивается (`DEDUP_TTL_SECONDS`, по умолчанию 3600с).
* `src/bot/handlers/dedup.py` + `TypeHandler(Update, …)` зарегистрирован
  в группе `-1` в `build_application` — при дубликате `update_id`
  поднимается `ApplicationHandlerStop` до того, как отработает любой
  реальный хэндлер.
* `src/tasks/delivery.py`:
  * 429 — `retry_after` читается и на верхнем уровне, и в
    `parameters.retry_after`; берётся больший.
  * 5xx → обычный `RuntimeError`, `autoretry_for=(Exception,)`
    обеспечивает экспоненциальный backoff с jitter.
  * Базовый класс `_DeliveryTask` с хуком `on_failure` публикует сырой
    JSON-record (`method`, `payload`, `reason`, `task_id`, `traceback`,
    `ts`) в `delivery_dlq` через producer-pool Celery (`throws=(Retry,)`
    защищает от срабатывания DLQ на Retry).
  * `delivery_dlq` объявлена в `celery_app.conf.task_queues` рядом с
    остальными — без консьюмера (ops дренируют вручную).
* `src/tasks/metrics.py`: счётчики
  `deliver_sent_total{method}`, `deliver_throttled_total{method}`,
  `deliver_server_error_total{method}`,
  `deliver_retried_total{method,reason}`,
  `deliver_dead_lettered_total{method,reason}`,
  `dedup_hit_total`, `dedup_miss_total`.
  Пока только in-process — HTTP `/metrics` отложен до Фазы 5.
* В `requirements.txt` добавлен `prometheus-client>=0.21.0`.
* Тесты (`tests/integration/test_idempotency.py`,
  `test_dedup_handler.py`, расширенный `test_delivery_task.py`):
  dedup hit/miss/TTL/строковые-vs-int ключи; `ApplicationHandlerStop`
  на дубликате; оба layout'а 429; путь retry для 5xx; публикация в DLQ
  из `on_failure` + метрика; падение DLQ-паблиша не маскирует исходное
  исключение. 98 тестов зелёные, покрытие 77.9%.

Деплоится: всё ещё одна реплика `bot`, но без дублирующей работы и
тихих падений.

### Фаза 2 — Вынести `webhook-receiver` ✅ выполнено

Цель: отделить приём от хэндлеров. Флот воркеров пока один, но
hot-path становится тривиальным.

**Сделано:**

* Новый пакет `src/receiver/`:
  * `app.py` — FastAPI-приложение с одним роутом: `POST /{bot_token}`
    (путь выводится из токена, так что у каждого бота свой URL).
  * Валидирует `X-Telegram-Bot-Api-Secret-Token`.
  * Переиспользует дедуп-хелпер Фазы 1 через `claim_update()`.
  * Публикует сырой JSON в брокер, кладя `chat_id` в заголовки
    сообщения (routing key прошит под `x-consistent-hash` exchange
    Фазы 3).
  * `/healthz` (liveness) + `/readyz` (Redis + брокер доступны).
* `publisher.py` — асинхронный aio-pika publisher с
  `connect_robust()` и publisher confirms. Объявляет `updates_queue`
  как durable. В Фазе 3 `exchange=""` заменяется на consistent-hash
  exchange; call sites не меняются.
* `chat_id.py` — best-effort извлечение `chat_id` из всех вариантов
  апдейтов (message, edited_*, channel_post, business_*,
  callback_query, business_connection, inline_query, …).
* `runner.py` — bootstrap uvicorn для `MODE=receiver`.
* `main.py` — ветка на `settings.mode == "receiver"` (polling /
  webhook / receiver). Один образ, разные команды.
* `src/config/settings.py` — новые env-переменные:
  `UPDATES_EXCHANGE` (по умолчанию `""` → встроенный direct exchange
  в Фазе 2), `UPDATES_QUEUE` (`updates_queue`), в `MODE` Literal
  добавлен `receiver`.
* `requirements.txt` — `fastapi`, `uvicorn[standard]`, `aio-pika`.
* k8s: новый overlay `k8s/overlays/scaled/` с Deployment
  `webhook-receiver` (базово 2 реплики), Service, Ingress, HPA
  (2–10 реплик на 70 % CPU). В ConfigMap overlay'я прибиты
  `MODE=receiver` и `UPDATES_QUEUE`.
* Тесты: 24 новых теста в `tests/integration/test_receiver.py` —
  happy path, неверный secret → 401, отсутствующий secret → 401,
  пустой secret (bypass), битый JSON → 400, нет `update_id` → 400,
  дубль → 200 без публикации, ошибка publisher'а → 503, апдейт без
  чата → публикуется с `chat_id=None`, `/healthz`, `/readyz` (ok /
  брокер отвалился / Redis недоступен) и параметризованная матрица
  для `extract_chat_id`.

Деплоится: `webhook-receiver` встаёт перед Telegram за публичным
Ingress и публикует в RabbitMQ. В Фазе 3 пишется шардированный
consumer, который заменяет одиночный Deployment `bot`. До этого
overlay `scaled` годится для staging-валидации producer-стороны —
в проде продолжайте крутить overlay `webhook`, пока Фаза 3 не
приземлилась.

### Фаза 3 — Consistent-hash шардинг + handler worker ✅ выполнено

Цель: запустить PTB-хэндлеры на N репликах без потери порядка per-chat.

**Сделано:**

* `k8s/base/rabbitmq.yaml` — включён плагин
  `rabbitmq_consistent_hash_exchange` через ConfigMap с монтированием
  `/etc/rabbitmq/enabled_plugins`.
* `src/tasks/broker_topology.py` — идемпотентный хелпер, объявляет
  exchange `updates` (`x-consistent-hash`, durable) и N
  шард-очередей `updates.shard.0`..`updates.shard.{N-1}`, каждую
  биндит с весом `"1"` (равномерное распределение по hash-кольцу).
  Когда `UPDATES_EXCHANGE=""` — откатывается в режим одной очереди
  Фазы 2.
* `src/config/settings.py` — новая переменная `UPDATES_SHARDS` (по
  умолчанию 16, ограничена `[1, 256]`) и хелпер `shard_queue_name(i)`.
* `src/receiver/publisher.py` — использует топологический хелпер;
  публикует с `routing_key=str(chat_id)` в режиме Фазы 3 и `"0"` для
  апдейтов без чата. `chat_id` едет в заголовках для Фазы 5
  (observability).
* `src/tasks/update_consumer.py` — долгоживущий PTB `Application`,
  собираемый один раз на worker-процесс (хук `worker_process_init`),
  на выделенном daemon-потоке крутится event loop, чтобы
  синхронные Celery-таски могли вызывать асинхронные хэндлеры через
  `run_coroutine_threadsafe`. Wiring хэндлеров тот же, что и в
  `build_application()` — нулевой дрейф между polling / webhook /
  receiver.
* `src/tasks/processing.py` — новая Celery-таска
  `handle_update(raw_update)` (`acks_late=True`, 3 ретрая с
  jitter-backoff), форвардит в `dispatch_update`.
* `src/tasks/celery_app.py` — шард-очереди объявляются динамически
  по `UPDATES_SHARDS`; хуки `worker_process_init` гардятся по наличию
  `-Q updates.shard.` в argv, так что delivery/tasks-воркеры остаются
  без PTB.
* k8s: `k8s/overlays/scaled/update-consumer.yaml` — StatefulSet
  (ordinal пода → индекс шарда), `--concurrency=1
  --prefetch-multiplier=1` для сохранения порядка per-chat. В
  ConfigMap overlay'я прибиты `UPDATES_EXCHANGE=updates` и
  `UPDATES_SHARDS=4`.
* Тесты: 24 новых (всего 146) — границы settings и `shard_queue_name`,
  `declare_updates_topology` (fallback на Фазу 2, форма Фазы 3,
  равномерность весов, краевой случай `shard_count=1`), routing
  publisher'а (выбор exchange Фазы 2 vs Фазы 3, маппинг `chat_id` →
  routing_key, fallback `"0"`, заголовки, пропагация ошибок
  брокера), `dispatch_update` (guard на не-инициализированного
  worker'а, feed в PTB, swallow при `de_json=None`, пропагация
  исключений для DLQ), тело таски `handle_update`.

Деплоится: хэндлеры масштабируются горизонтально; порядок per-chat
сохранён `x-consistent-hash`'ем + одиночным consumer'ом на шард.
После cutover'а заменяет одиночный Deployment `bot`.

### Фаза 4 — Миграция SQLite → PostgreSQL

Цель: снять ограничение на одну реплику, завязанное на SQLite и RWO
PVC, и сделать флот хэндлеров по-настоящему горизонтальным. Разбита
на четыре подфазы, чтобы смена бэкенда и копирование данных
откатывались независимо.

#### 4.1 Код Postgres-бэкенда

* Новый пакет `src/databases/postgres/`, зеркалит
  `src/databases/sqlite/`:
  * `database.py` (такое же имя, как в `src/databases/sqlite/`) — async
    engine/session factory через `sqlalchemy.ext.asyncio` + `asyncpg`;
    синхронный engine через `psycopg` для Alembic.
  * `models.py` — те же таблицы, но с нативными типами Postgres:
    * UUID v7 → `UUID` (а не `text(36)`). Хранится как нативный
      `uuid`; v7-биты по-прежнему дают монотонные вставки в btree.
    * Timestamps → `TIMESTAMPTZ` (в SQLite был naive ISO-текст).
    * Booleans → `BOOLEAN` (в SQLite был `INTEGER 0/1`).
    * JSON-payloads (если появятся) → `JSONB`.
  * `repositories/` — реализовать все `I*Repository` из
    `src/databases/interfaces/`. Использовать `INSERT ... ON CONFLICT`
    для upsert-путей (в SQLite было `INSERT OR REPLACE`).
* Регистрация в `src/databases/factory.py`:
  `DATABASE_BACKEND=postgres` → вернуть Postgres-реализацию.
  SQLite остаётся дефолтом для локальной разработки.
* Конфиг: `DATABASE_URL` уже единственный рычаг. Производные
  `database_url_sync` / `database_url_async` в `settings.py`
  настраиваются так, чтобы выдавать `postgresql+psycopg://` и
  `postgresql+asyncpg://`.
* Зависимости: в `requirements.txt` добавить `asyncpg` и
  `psycopg[binary]`; в `requirements-dev.txt` —
  `testcontainers[postgres]`.

**Поставлено (4.1):**

* `src/databases/postgres/` — зеркало `src/databases/sqlite/` (та же
  раскладка модулей, те же DTO):
  * `models/base.py` — `UuidV7PrimaryKeyMixin` использует
    `sqlalchemy.dialects.postgresql.UUID(as_uuid=False)`, чтобы DTO
    (`id: str`) оставались диалект-независимыми;
    `CreatedAtMixin` / `TimestampMixin` дают `TIMESTAMPTZ` через
    `DateTime(timezone=True)`.
  * `models/{user,business_connection,message_mapping,kv_store}.py` —
    нативный `BOOLEAN`; у `kv_store` появился unique-констрейнт
    `uq_kv_store_owner_key`, на который опирается upsert.
  * `repositories/` — upsert'ы в один round-trip через
    `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(constraint=...)`
    + `RETURNING`; read-пути без изменений.
  * `database.py` — `create_async_engine` с пулом
    (`pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`); без
    SQLite-специфичного `check_same_thread`.
* `src/databases/factory.py` — `DATABASE_BACKEND=postgres` → возвращает
  `PostgresDatabase(settings.database_url)`. SQLite остаётся дефолтом.
* `src/config/settings.py` — `database_backend: Literal["sqlite", "postgres"]`;
  новый `POSTGRES_DSN`; `_rewrite_postgres_driver` нормализует
  `postgres://` / `postgresql://` / `postgresql+<driver>://` и
  подменяет драйвер на `asyncpg` (async-путь) либо `psycopg`
  (Alembic, sync-путь). `DATABASE_URL` / `DATABASE_URL_SYNC`
  производные от этого.
* `requirements.txt` — `asyncpg>=0.30.0`, `psycopg[binary]>=3.2.0`.
* Тесты: 21 новый (всего 167) — edge-кейсы DSN-ревайтера (голый
  `postgres://`, уже проставленный драйвер, query-string, запрещённые
  схемы), настройки (async→asyncpg, sync→psycopg, sqlite без
  изменений), CREATE TABLE DDL проверяет нативные `UUID` /
  `TIMESTAMPTZ` / `BOOLEAN`, фабрика диспатчит (sqlite/postgres без
  коннекта; неизвестный бэкенд режется pydantic `Literal`).
* Ещё впереди (4.2+): Alembic `0002_postgres_parity.py`, скрипт
  миграции данных, k8s-оверлей, runbook cutover'а.

#### 4.2 Alembic: миграции на два диалекта

Текущий `alembic/versions/0001_initial_schema.py` написан под SQLite.
Варианты от самого чистого к прагматичному:

* **Предпочтительно: replay, а не перевод.** Оставить 0001 как есть
  для истории SQLite, добавить `0002_postgres_parity.py` — no-op на
  SQLite, создающий параллельную схему на Postgres. Оба бэкенда
  гоняются одним `alembic upgrade head`.

  Внутри миграции:
  ```python
  if context.get_bind().dialect.name == "postgresql":
      # CREATE TABLE ... с UUID / TIMESTAMPTZ
  else:
      # pass — в SQLite таблицы уже созданы из 0001
  ```

* **Альтернатива: разные version-таблицы.** Две ветки Alembic
  (`--rev-id` с префиксом под каждый диалект). Больше движущихся
  частей; избегать, если схемы реально не расходятся.

Политика дальше: каждая новая миграция тестируется на **обоих**
диалектах в CI (testcontainers Postgres + in-memory SQLite).

* `alembic/env.py` не меняется — он уже читает
  `settings.database_url_sync`.
* `migrate-job.yaml` (k8s) не меняется — просто запускает
  `alembic upgrade head` с новым URL.

#### 4.3 Миграция данных (одноразовая, v2 SQLite → v3 Postgres)

Допущение: в проде небольшой датасет (сотни пользователей, десятки
business-соединений, ограниченные KV-записи). Потоковое bulk-копирование
в коротком maintenance window выигрывает у dual-write-механики.

Процедура оформлена как `scripts/migrate_sqlite_to_postgres.py`:

1. **Preflight.**
   * Проверить URL источника (`sqlite://…`) и цели (`postgresql://…`).
   * `alembic upgrade head` против Postgres (пустая схема → полностью
     мигрированная схема).
   * Убедиться, что row count на цели — ноль для каждой таблицы;
     иначе отказаться запускаться против непустой Postgres.

2. **Заморозить записи.**
   * Скейлнуть `bot` / `worker-tasks` / `worker-delivery` в 0 реплик.
   * Оставить `webhook-receiver` поднятым, *если* Фаза 2 уже в проде:
     receiver продолжает отвечать 200, апдейты копятся в RabbitMQ —
     ноль downtime с точки зрения пользователя, помимо окна заморозки.
   * Иначе: закрыть Telegram в окно 503, снять webhook или навести
     его на статику. Описать в runbook'е.

3. **Снимок источника.**
   * `sqlite3 source.db ".backup snapshot.db"` (консистентная
     hot-копия).
   * Дальше работаем со `snapshot.db`; оригинал не трогаем до
     верификации (якорь для отката).

4. **Копируем по таблицам в FK-безопасном порядке.**
   * Читаем из SQLite батчами по `BATCH_SIZE` (например, 1000) через
     существующие SQLAlchemy-модели.
   * Трансформируем построчно:
     * UUID-строки → `uuid.UUID(s)`.
     * ISO/epoch-таймстемпы → `datetime` с UTC tz.
     * `0/1` в bool-колонках → `bool`.
   * Bulk-insert в Postgres через `INSERT ... ON CONFLICT DO NOTHING`
     — скрипт становится resumable при падении.
   * Таблицы пока небольшие; если что-то разрастётся (например,
     message mappings) — переходим на `COPY` через `psycopg.copy`.

5. **Сброс sequence / identity-колонок** — неактуально (UUID v7, без
   autoincrement), но скрипт проверяет, что `serial`/`identity`
   случайно не появились.

6. **Верификация.**
   * Совпадение row count по таблицам.
   * Spot-check: по каждой таблице выбрать 10 случайных PK из SQLite
     и убедиться, что все колонки (после нормализации типов)
     совпадают на Postgres.
   * FK-целостность: `SELECT` self-join на Postgres, чтобы убедиться,
     что нет orphaned-строк (не должно быть, если порядок соблюдён;
     подстраховка).
   * Отчёт о миграции (JSON) в `backups/migration-<ts>.json`:
     source row counts, target row counts, длительность, пропущенные
     строки.

7. **Переключение конфига.**
   * `DATABASE_BACKEND=postgres`, `DATABASE_URL=postgres://…` в
     ConfigMap/Secret.
   * `kubectl rollout restart deployment/worker-tasks
     deployment/worker-delivery webhook-receiver`.
   * Вернуть нормальное количество реплик.
   * Накопившиеся в RabbitMQ апдейты дренируются.

8. **Post-migration soak (24ч).**
   * SQLite PVC + снапшот хранить 7 дней.
   * Мониторить: handler error rate, глубину `delivery_queue`,
     количество соединений к Postgres, slow-query log.

Тесты для самого скрипта миграции (integration):
* Наполнить SQLite фикстурой → запустить скрипт → убедиться в
  row-for-row соответствии на testcontainer Postgres.
* Падение на середине → повторный запуск → idempotent (за счёт
  `ON CONFLICT`).
* Отказ запускаться против непустой цели.
* Отказ запускаться, если Alembic head цели не совпадает.

#### 4.4 План отката

Пока SQLite не удалён (хранится 7 дней после cutover):

* **Config rollback** (<2 мин): переключить
  `DATABASE_BACKEND=sqlite`, вернуть mount тома, ролл воркеров.
  Теряются записи, сделанные в Postgres после cutover — приемлемо
  только если проблему ловим в первые минуты.
* **Data rollback** (<30 мин): выгрузить дельты Postgres с момента
  cutover (все строки с `updated_at > cutover_ts`), вручную
  смержить в SQLite-снапшот через симметричный
  `scripts/migrate_postgres_to_sqlite.py`. Использовать только если
  приложение подтверждённо сломано и простой допустим.
* **Nuclear**: восстановить SQLite-снапшот из Фазы 0, смириться с
  потерей записей. Только если предыдущие варианты не подходят.

После 7 дней soak: удалить PVC `tg-bot-data`, убрать SQLite-бэкенд
из дефолта factory (код оставить для dev), переименовать
`backup-sqlite` CronJob в `backup-postgres` (`pg_dump -Fc`, gzip, тот
же PVC + retention).

#### 4.5 Изменения в k8s и ops

* Новый `k8s/base/postgres.yaml`:
  * `StatefulSet` на одну реплику для self-hosted варианта (HA — не
    цель, одной Postgres хватит для нагрузки этого бота). PVC под
    данные, умеренные requests (256Mi–1Gi, 100m–500m CPU).
  * `Service` (ClusterIP) на `5432`.
  * Учётки через Secret (`POSTGRES_USER`, `POSTGRES_PASSWORD`,
    `POSTGRES_DB`).
* `k8s/base/kustomization.yaml`: подключить новый файл.
* Overlay `k8s/overlays/managed-postgres/` (опционально): убирает
  StatefulSet + Service, полагается на `DATABASE_URL` из Secret,
  указывающий на managed-провайдера.
* `k8s/base/backup-cronjobs.yaml`: добавить `backup-postgres`
  (`pg_dump -Fc`, ежедневно в 02:45 UTC, retention 30 дней).
  `backup-sqlite` отключить или удалить после soak.
* `worker-tasks` / `worker-delivery`: убрать mount PVC `tg-bot-data`
  — они больше не трогают файловую БД.
* `migrate-job.yaml`: без изменений, но убедиться, что он
  отрабатывает с новым URL до старта воркеров.

Деплой-итог: `worker-tasks` можно запускать в любом количестве
реплик; SQLite остаётся пригодным для локальной разработки
(`DATABASE_BACKEND=sqlite` в `.env`).

### Фаза 5 — Автоскейлинг и observability

Цель: сделать систему саморегулируемой и прозрачной.

* HPA:
  * `webhook-receiver`: CPU + RPS (через Prometheus adapter или
    просто CPU).
  * `worker-tasks`: KEDA `rabbitmq` scaler по сумме глубины очередей
    шардов. Min = количество шардов (ограничение порядка), max =
    количество шардов × K — см. примечание ниже.
  * `worker-delivery`: фиксированный либо KEDA по глубине
    `delivery_queue`, но ограничен глобальной скоростью Telegram.
* Метрики Prometheus (receiver publish rate, dedup hits, глубина
  шард-очередей, handler latency, 429-rate в deliver).
* Опционально: Grafana dashboard JSON в `k8s/base/`.

Про порядок: если `worker-tasks` масштабируется выше N (количества
шардов), лишние реплики не могут потреблять упорядоченные шарды без
нарушения правила «один потребитель». Два варианта:
(а) держать `replicas == shards` и поднимать `UPDATES_SHARDS` для
роста (нужен ребаланс — см. §5);
(б) разрешить лишним репликам потреблять *неупорядоченные* очереди
(например, chat-less апдейты: `callback_query` без `message.chat`).

### Фаза 6 — Cutover и уборка

* `setWebhook` → публичный URL receiver'а.
* Удалить старый `bot` Deployment/Service/Ingress; polling overlay
  можно оставить ради локального паритета.
* Убрать ветку webhook из `src/bot/runner.py` (polling остаётся для
  dev).
* Обновить `README.md` — снять баннер «v3 status: in progress», внести
  секцию про масштабирование в основное описание архитектуры.

---

## 4. Реестр рисков

| Риск | Митигация |
|---|---|
| Ретраи Telegram во время cutover Фазы 2 дублируют работу | Фаза 1 с дедупом должна приехать раньше |
| Плагин consistent-hash не включён на существующем RabbitMQ | Фаза 3 добавляет его в манифесты; план rolling-restart документирован ниже |
| Решардинг (смена `UPDATES_SHARDS`) временно ломает порядок | Оформить как maintenance window; дренировать очереди до смены |
| Долгий хэндлер забивает шард | Жёсткий timeout + DLQ на падение хэндлера; тяжёлая работа остаётся на `worker-delivery`/выделенных очередях |
| Потеря данных при миграции на Postgres | SQLite-снапшот хранится 7 дней после cutover; скрипт миграции идемпотентен через `ON CONFLICT DO NOTHING`; обратный скрипт `migrate_postgres_to_sqlite.py` для мягкого отката (см. §4.4) |
| Баг приведения типов при копии SQLite→Postgres (tz-naive timestamps, UUID-as-text, bool-as-int) | Построчная трансформация централизована в одной функции, покрыта integration-тестами с diff каждой колонки на случайной выборке |
| Непустой Postgres случайно перезаписан повторным запуском | Скрипт миграции отказывается запускаться, если row count на цели ненулевой или Alembic head не совпадает |
| Падение на середине миграции | Скрипт resumable: идемпотентные insert'ы + прогресс по таблицам логируется в JSON-отчёт |
| Чрезмерный скейлинг `worker-delivery` выводит на глобальный 429 Telegram | Держать фиксированные реплики, полагаться на Redis token-bucket как на горлышко |
| PTB `Application` не рассчитан на обработку произвольного `Update` JSON вне контекста | Прототипировать подключение хэндлеров в Фазе 3 заранее; при необходимости откатиться на минимальный собственный диспатчер |

---

## 5. Процедура решардинга (ops-заметка)

Смена `UPDATES_SHARDS` меняет hash-кольцо → один и тот же `chat_id`
может попасть в другой шард → кратковременный слом порядка per-chat.

Документированный runbook:
1. Объявить maintenance window (секунды).
2. Остановить `webhook-receiver` (или направить Telegram webhook на
   503-страницу).
3. Дренировать все очереди `updates.shard.*` (ждать, пока пустые).
4. Применить новое количество шардов через kustomize
   (`UPDATES_SHARDS`, replicas у StatefulSet).
5. Поднять `webhook-receiver`.

Для стартового сайзинга выбирайте `UPDATES_SHARDS` с запасом (16–32).
Количество реплик воркеров равно количеству шардов; CPU обычно
становится bottleneck'ом гораздо раньше числа шардов.

---

## 6. Вне скоупа v3

* Multi-tenant / несколько bot-токенов.
* Миграция на Kafka / NATS.
* gRPC между receiver и воркерами (оставляем обмен через брокер).
* Backup сообщений RabbitMQ на уровне payload'ов (уже явно out of
  scope в v2).
* Замена Celery на собственный консьюмер (Celery + Kombu умеют
  рулить consistent-hash exchange через `task_queues` с
  кастомными биндингами).

---

## 7. Чеклист деливераблов

- [x] Фаза 1: дедуп + 429 + DLQ + метрики + тесты
- [x] Фаза 2: `webhook-receiver` + `MODE=receiver` + overlay `scaled` + тесты
- [x] Фаза 3: consistent-hash exchange + таска `handle_update` +
      StatefulSet-на-шард + тесты порядка
- [x] Фаза 4.1: бэкенд `src/databases/postgres/` + регистрация в factory + зависимости
- [ ] Фаза 4.2: dual-dialect Alembic-миграция + CI-прогон на обоих диалектах
- [ ] Фаза 4.3: `scripts/migrate_sqlite_to_postgres.py` + integration-тесты (копия фикстуры, idempotency, отказы)
- [ ] Фаза 4.4: обратный скрипт + задокументированный runbook отката
- [ ] Фаза 4.5: `k8s/base/postgres.yaml` + overlay `managed-postgres` + CronJob `backup-postgres`; снятие mount PVC `tg-bot-data` с воркеров
- [ ] Фаза 5: HPA + KEDA + метрики Prometheus + dashboard
- [ ] Фаза 6: cutover, уборка, консолидация README
