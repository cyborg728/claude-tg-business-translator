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

### Фаза 1 — Идемпотентность и упрочнение rate-limit (safety net)

Цель: починить корректность до смены топологии.

* Добавить хелпер дедупа по `update_id`: `cache/idempotency.py` с
  `SETNX` + TTL.
* Подключить его в существующем PTB webhook-хэндлере (ранний отбой
  дубликатов).
* `src/tasks/delivery.py`:
  * Парсить 429-ответ, учитывать `parameters.retry_after`.
  * Добавить DLQ (`delivery_dlq`) для терминальных падений после N
    ретраев.
  * Экспортировать счётчики в Prometheus (sent, 429, 5xx, retried,
    dead-lettered).
* Тесты: dedup hit/miss, TTL; 429→retry с правильным countdown; DLQ.

Деплоится: всё ещё одна реплика `bot`, но без дублирующей работы и
тихих падений.

### Фаза 2 — Вынести `webhook-receiver`

Цель: отделить приём от хэндлеров. Флот воркеров пока один, но
hot-path становится тривиальным.

* Новый пакет `src/receiver/`:
  * `app.py` — FastAPI (или aiohttp) приложение с одним роутом:
    `POST /webhook/{secret_path}`.
  * Валидирует `X-Telegram-Bot-Api-Secret-Token`.
  * Использует дедуп-хелпер из Фазы 1.
  * Публикует сырой JSON в exchange `updates` с
    `routing_key=str(chat_id)`.
  * `/healthz` + `/readyz`.
* `main.py`: добавить ветку `MODE=receiver` (polling / webhook /
  receiver). Остаётся один образ — k8s Deployment просто
  переопределяет команду.
* `src/tasks/receiver_publish.py`: тонкая обёртка над aio-pika /
  kombu producer. Переиспользуется и из PTB-in-process пути (чтобы
  Фазу 2 можно было выкатить даже если Фаза 3 ещё не готова).
* k8s: новый overlay `k8s/overlays/scaled/` (копия `webhook/` +
  `webhook-receiver` Deployment, Service, HPA).
* Тесты: receiver happy path, плохой secret → 401, дубль → 200 без
  публикации, ошибка публикации → 503.

Деплоится: `webhook-receiver` может стоять перед Telegram; хэндлеры
всё ещё в одном `bot`-поде, потребляющем из шард-очередей (Фаза 3
превращает это в полноценный шардинг; пока одна очередь). На этом шаге
`bot` Deployment теряет Ingress — Telegram общается только с
receiver'ом.

### Фаза 3 — Consistent-hash шардинг + handler worker

Цель: запустить PTB-хэндлеры на N репликах без потери порядка per-chat.

* Включить плагин RabbitMQ `rabbitmq_consistent_hash_exchange`
  (добавить в `k8s/base/rabbitmq.yaml` `enabled_plugins`).
* Объявить exchange + шарды в `src/tasks/celery_app.py`:
  * Exchange `updates`, тип `x-consistent-hash`.
  * N биндингов в `updates.shard.0` … `updates.shard.{N-1}`,
    routing key каждого биндинга = вес (например `"1"`).
  * Количество шардов через конфиг `UPDATES_SHARDS` (по умолчанию 16).
* Новая Celery-таска `handle_update(raw_update: dict)` в
  `src/tasks/processing.py`:
  * Восстанавливает `telegram.Update.de_json(...)` через общий `Bot`.
  * Скармливает в долгоживущий PTB `Application`, собранный в
    `worker_process_init` (один на worker-процесс). Код хэндлеров не
    меняется.
  * Критично: каждая шард-очередь потребляется с `prefetch_count=1` и
    Celery `worker_concurrency=1` **на шард** (т.е. один worker-pod на
    шард, либо шард на выделенный процесс через `-Q`). Именно это
    сохраняет порядок.
* Топология Deployment'ов:
  * `worker-tasks` становится параметризованным набором — например,
    `StatefulSet` из N реплик, каждая потребляет ровно один шард
    (`-Q updates.shard.$(POD_INDEX)`), либо Deployment на шард через
    kustomize-генератор. StatefulSet проще.
* Удалить Celery-producer из `bot`-процесса — мёртвый код после Фазы 2.
* Тесты: воспроизводимость consistent-hash (один `chat_id` → один
  шард); property-test на порядок (1000 апдейтов одного чата → в
  порядке у потребителя); smoke-тест на подключение хэндлеров.

Деплоится: хэндлеры масштабируются горизонтально; порядок per-chat
сохранён.

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

- [ ] Фаза 1: дедуп + 429 + DLQ + метрики + тесты
- [ ] Фаза 2: `webhook-receiver` + `MODE=receiver` + overlay `scaled` + тесты
- [ ] Фаза 3: consistent-hash exchange + таска `handle_update` +
      StatefulSet-на-шард + тесты порядка
- [ ] Фаза 4.1: бэкенд `src/databases/postgres/` + регистрация в factory + зависимости
- [ ] Фаза 4.2: dual-dialect Alembic-миграция + CI-прогон на обоих диалектах
- [ ] Фаза 4.3: `scripts/migrate_sqlite_to_postgres.py` + integration-тесты (копия фикстуры, idempotency, отказы)
- [ ] Фаза 4.4: обратный скрипт + задокументированный runbook отката
- [ ] Фаза 4.5: `k8s/base/postgres.yaml` + overlay `managed-postgres` + CronJob `backup-postgres`; снятие mount PVC `tg-bot-data` с воркеров
- [ ] Фаза 5: HPA + KEDA + метрики Prometheus + dashboard
- [ ] Фаза 6: cutover, уборка, консолидация README
