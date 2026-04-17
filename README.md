# Telegram Business Bot — v3

Foundational scaffolding for a Telegram Business Account bot. Built around
**python-telegram-bot 22.x**, **Celery + RabbitMQ**, **Redis**, **SQLite or
Postgres + Alembic** and **Fluent** for i18n. Every piece is swappable —
all repositories sit behind interfaces under `src/databases/`, and
`DATABASE_BACKEND={sqlite,postgres}` picks the concrete implementation.

> **v3 status — Phase 5 shipped.** v3 inherits the v2 code and adds
> horizontal scalability: the webhook receiver is split out from the PTB
> handler process, updates are sharded by `chat_id` into RabbitMQ, and
> delivery is token-bucketed against Telegram's global / per-chat rate
> limits. **Phase 1** — `update_id` dedup, 429 `retry_after` handling,
> `delivery_dlq`, Prometheus counters. **Phase 2** — stateless
> `webhook-receiver` (FastAPI + aio-pika), `MODE=receiver`, the
> `scaled` overlay with HPA. **Phase 3** — `x-consistent-hash`
> exchange + N shard queues, `handle_update` Celery task with a
> long-lived per-worker PTB `Application`, `update-consumer`
> StatefulSet that preserves per-chat ordering. **Phase 4.1** —
> `src/databases/postgres/` backend with native `UUID` /
> `TIMESTAMPTZ` / `BOOLEAN` and `INSERT ... ON CONFLICT` upserts;
> DTOs carry `id: uuid.UUID`. **Phase 4.2** — dual-dialect Alembic
> chain (`0002_postgres_parity`): one `alembic upgrade head` works
> against either backend; `env.py` picks the dialect-native `Base`.
> **Phase 4.3** — `scripts/migrate_sqlite_to_postgres.py`: one-shot
> data copy with preflight (refuses non-empty targets), batched
> `INSERT ... ON CONFLICT DO NOTHING`, row-count verification, and a
> JSON migration report. **Phase 4.4** —
> `scripts/migrate_postgres_to_sqlite.py`: symmetric reverse copier
> for tier-2 rollback, with a three-tier runbook. **Phase 4.5** —
> k8s Postgres manifests, `backup-postgres` CronJob,
> `managed-postgres` overlay, workers decoupled from the SQLite PVC.
> **Phase 5** — `/metrics` endpoint (Prometheus), KEDA ScaledObjects
> for `worker-delivery` + `worker-tasks` on RabbitMQ queue depth,
> ServiceMonitor + PodMonitor, Grafana dashboard JSON, receiver +
> handler instrumentation.
> See [`MIGRATION_V2_TO_V3.md`](./MIGRATION_V2_TO_V3.md) for the
> step-by-step plan and [🎯 v3 — horizontal scaling](#-v3--horizontal-scaling)
> below for the target architecture.

---

## 🎯 v3 — horizontal scaling

v2 works, but has two bottlenecks baked in:

1. **Webhook + handlers live in the same process.** PTB's webhook server
   and every handler share one event loop. A long LLM call behind `/start`
   can delay the 200 OK to Telegram → retries → duplicate work.
2. **Bot Deployment is single-replica on purpose.** Telegram allows exactly
   one webhook URL, and PTB is stateful (update offsets, per-chat locks
   inside the process). Scaling the current `bot` Deployment past 1 replica
   breaks ordering and duplicates sends.

v3 removes both by splitting responsibilities across three tiers, all
stateless except the brokers:

```
Telegram ─HTTPS─▶ Ingress ─▶ webhook-receiver (Deployment, HPA)
                                     │  publish(update, key=chat_id)
                                     ▼
                              RabbitMQ (consistent-hash exchange → N chat-shards)
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
       update-consumer                            worker-delivery
     (StatefulSet per shard,                     (generic deliver task,
      long-lived PTB app +                        Redis token-bucket,
      handlers + LLM,                             fixed replicas)
      --concurrency=1)                                   │
                 │                                       ▼
                 ▼                                 Telegram Bot API
              DB / Redis
```

Highlights:

* **`webhook-receiver`** is a thin FastAPI/aiohttp service. It validates
  Telegram's `X-Telegram-Bot-Api-Secret-Token`, dedupes on `update_id`
  (Redis `SETNX`) and publishes the raw update to RabbitMQ. No PTB, no
  DB, no LLM. HPA scales it by RPS / CPU.
* **Ordering per chat** is preserved via a RabbitMQ
  [`x-consistent-hash`](https://github.com/rabbitmq/rabbitmq-consistent-hash-exchange)
  exchange keyed on `chat_id`, fanning into N chat-shard queues. Each
  shard queue has a single in-flight consumer, so messages from one chat
  are serialized; different chats run in parallel across shards and worker
  replicas.
* **`update-consumer`** (Phase 3) runs the PTB handlers outside the webhook
  hot path. Each pod consumes exactly one `updates.shard.<i>` queue
  (`--concurrency=1 --prefetch-multiplier=1`), so per-chat ordering is
  preserved. The update is reconstructed from the JSON payload via
  `Update.de_json` and fed through the existing dispatcher — handler
  code stays unchanged. The pre-Phase-3 `tasks_queue` + generic
  `worker-tasks` Deployment remains for slow side-tasks (smoke, future
  LLM jobs) that don't need per-chat ordering.
* **`worker-delivery`** stays as-is conceptually (generic `deliver` task +
  facade), but the rate-limiter is hardened: global bucket + per-chat
  bucket + 429 `retry_after` honoring + circuit-break to DLQ after N
  retries.
* **State moves off the bot process.** SQLite on an RWO PVC is fine for
  v2's single replica but blocks multi-replica workers; Phase 4.1 adds a
  `src/databases/postgres/` backend (native `UUID` / `TIMESTAMPTZ` /
  `BOOLEAN`, `INSERT ... ON CONFLICT` upserts, `asyncpg` for the app
  path and `psycopg` for Alembic). Phase 4.2 wires Alembic up as a
  dual-dialect chain — `0002_postgres_parity` is a no-op on SQLite and
  builds the Postgres-native schema (UUID / TIMESTAMPTZ / BOOLEAN +
  `uq_kv_store_owner_key`) on Postgres, so one `alembic upgrade head`
  works against either backend. Phase 4.3 adds
  `scripts/migrate_sqlite_to_postgres.py` — a one-shot data copy that
  preflights (refuses non-empty targets), batches
  `INSERT ... ON CONFLICT DO NOTHING`, coerces UUID/tz/bool types, and
  writes a JSON migration report. Flip with
  `DATABASE_BACKEND=postgres` + `POSTGRES_DSN=postgresql://…`. Phase
  4.4 adds `scripts/migrate_postgres_to_sqlite.py`: the symmetric
  reverse copier used for tier-2 rollback (§4.4 in the migration
  doc — config flip / reverse copy / nuclear snapshot restore).
  Phase 4.5 ships the k8s Postgres manifests: `postgres.yaml`
  (StatefulSet + Service + PVC), a `backup-postgres` CronJob
  (`pg_dump -Fc`, suspended until cutover), a `managed-postgres`
  overlay that strips the in-cluster Postgres and points at a managed
  endpoint, and drops the SQLite PVC mount from all workers so
  `worker-tasks` can scale to any replica count.
* **k8s**: new overlay `k8s/overlays/scaled` with `webhook-receiver`
  Deployment + Service + Ingress, HPAs (CPU and KEDA/RabbitMQ queue
  depth), and the existing worker Deployments reused.

Non-goals for v3:
* Multi-bot / multi-tenancy — one bot token, one webhook.
* Kafka or NATS — RabbitMQ's consistent-hash + Celery is enough at the
  expected traffic and keeps the v2 stack.
* Blue/green of the webhook URL — `setWebhook` is atomic and fast enough;
  brief duplicate-during-switch is handled by `update_id` dedup.

---

## ✨ What's inside

| Feature                                | How it's implemented                                              |
| -------------------------------------- | ----------------------------------------------------------------- |
| Commands `/start`, `/smoke`            | `src/bot/handlers/`, dispatched non-blocking                      |
| Commands `/redis_save`, `/redis_read`  | Ephemeral text stash via async Redis                              |
| Polling ⇄ Webhook ⇄ Receiver switch    | `MODE=polling|webhook|receiver` in `.env` / ConfigMap             |
| **`webhook-receiver` (Phase 2)**       | FastAPI + aio-pika, `src/receiver/`, `scaled` overlay + HPA       |
| **Consistent-hash shards (Phase 3)**   | `x-consistent-hash` exchange + N `updates.shard.<i>` queues       |
| **`update-consumer` (Phase 3)**        | Per-shard StatefulSet, long-lived PTB app per worker process      |
| Multi-language                         | `fluent.runtime`, auto-picks the user's Telegram `language_code`  |
| **Update dedup (`update_id`)**         | Redis `SET NX EX` via `TypeHandler` in group `-1` (Phase 1)       |
| Task queue (`tasks_queue`)             | Celery worker processes slow work                                 |
| Delivery queue (`delivery_queue`)      | Celery worker with per-sec / per-chat rate limits in Redis        |
| **Delivery DLQ (`delivery_dlq`)**      | Terminal failures published raw via Kombu; no consumer (Phase 1)  |
| **Prometheus metrics**                 | `/metrics` endpoint on receiver; counters + histograms via `prometheus-client` |
| Business accounts (`business_message`) | `BusinessConnectionHandler` + dedicated `MessageHandler` filter   |
| Error handling                         | Global `error_handler` + catch-all `/unknown` handler             |
| UUID v7 primary keys                   | `uuid_utils.uuid7` via `src/utils/ids.py`                         |
| Alembic migrations                     | Dual-dialect: `0001_initial_schema.py` (SQLite) + `0002_postgres_parity.py` (Postgres) |
| k3s manifests                          | Kustomize `base/` + `overlays/{polling,webhook,scaled,managed-postgres}` |
| DB & RabbitMQ backups                  | CronJobs (SQLite + Postgres + RabbitMQ) to a dedicated PVC; local shell equivalents in `scripts` |
| Test suite                             | `pytest` — unit + SQLite + fakeredis + Alembic (see "Tests")      |

---

## 🗂 Project layout

```
.
├── main.py                       # Entrypoint (reads MODE and runs polling/webhook)
├── alembic/                      # Dual-dialect Alembic migrations
│   ├── env.py                    # Picks dialect-native Base from settings.database_url_sync
│   └── versions/
│       ├── 0001_initial_schema.py      # SQLite schema; no-op on Postgres
│       └── 0002_postgres_parity.py     # Postgres UUID / TIMESTAMPTZ; no-op on SQLite
├── alembic.ini
├── requirements.txt
├── Dockerfile
├── docker-compose.yml            # Local dev stack: bot + workers + RabbitMQ + Redis
├── .env.example                  # Copy to .env
├── scripts/
│   ├── backup_sqlite.sh
│   ├── backup_rabbitmq.sh
│   ├── migrate_sqlite_to_postgres.py  # Phase 4.3 v2→v3 data copy
│   └── migrate_postgres_to_sqlite.py  # Phase 4.4 tier-2 rollback copier
├── k8s/
│   ├── base/                     # Namespace, ConfigMap, Secret, PVCs, Redis, RabbitMQ,
│   │                             # Postgres StatefulSet+Service, worker deployments,
│   │                             # migrate Job, backup CronJobs (SQLite+Postgres+RMQ)
│   └── overlays/
│       ├── polling/              # MODE=polling — single-replica bot Deployment
│       ├── webhook/              # MODE=webhook — bot Deployment + Service + Ingress
│       ├── scaled/               # MODE=receiver + consistent-hash shards (Phase 2+3)
│       └── managed-postgres/     # Strips self-hosted PG; points at RDS / Cloud SQL
└── src/
    ├── config/settings.py        # Pydantic Settings (single source of truth)
    ├── bot/
    │   ├── application.py        # PTB ApplicationBuilder + handlers wiring
    │   ├── runner.py             # polling / webhook bootstrap
    │   ├── deps.py               # BotDeps dataclass passed into handlers
    │   └── handlers/
    │       ├── commands.py       # /start + unknown
    │       ├── smoke.py         # /smoke (end-to-end pipeline sanity check)
    │       ├── redis_cmd.py      # /redis_save, /redis_read
    │       ├── business.py       # business_connection + business_message
    │       └── errors.py         # global error handler
    ├── cache/redis_client.py     # async redis — /redis_save, /redis_read, save-flag
    ├── databases/
    │   ├── factory.py            # Picks the backend from settings.database_backend
    │   ├── interfaces/           # AbstractDatabase + I<Entity>Repository (backend-agnostic)
    │   ├── sqlite/               # SQLite implementation (CHAR(36) uuid via TypeDecorator)
    │   └── postgres/             # Postgres implementation (native UUID / TIMESTAMPTZ, ON CONFLICT)
    ├── i18n/
    │   ├── translator.py         # Fluent-based per-user translator
    │   └── locales/
    │       ├── en/main.ftl
    │       └── ru/main.ftl
    ├── tasks/
    │   ├── celery_app.py         # Celery app + queue routing
    │   ├── processing.py         # Heavy work → tasks_queue
    │   └── delivery.py           # Rate-limited send → delivery_queue
    └── utils/ids.py              # uuid7 / uuid7_str helpers
```

Adding a new database backend? Mirror `src/databases/sqlite/` /
`src/databases/postgres/`, implement the same `AbstractDatabase` +
`I*Repository` classes and register it in `src/databases/factory.py`. No
handler code changes.

---

## 🚀 Quick start (local)

```bash
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN

# Option A — Docker Compose (full stack)
docker compose up --build

# Option B — bare metal
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
# in 3 terminals:
python main.py                                                  # bot
celery -A src.tasks.celery_app worker -Q tasks_queue -c 4      # processing worker
celery -A src.tasks.celery_app worker -Q delivery_queue -c 2   # delivery worker
```

Ensure you have RabbitMQ and Redis running locally (Docker Compose does it
for you).

Try the commands in your Telegram client:

| Command         | Effect                                                            |
| --------------- | ----------------------------------------------------------------- |
| `/start`        | Stores / refreshes your user row and sends a localized greeting   |
| `/smoke`        | Replies "queued", Celery worker sleeps 5s, delivery worker sends `success` |
| `/redis_save`   | Puts you in "save mode" — next text message is stored in Redis    |
| `/redis_read`   | Reads back the text you last saved                                |

---

## 🌍 i18n

Strings live in `src/i18n/locales/<locale>/main.ftl` (Project Fluent syntax).
The translator auto-discovers every directory at startup; add a new language
by dropping in `src/i18n/locales/de/main.ftl` — no code change needed.

The bot resolves the user's locale per message using Telegram's
`User.language_code`. First-time users get their own language automatically;
returning users inherit whatever we persisted in `users.language_code`.

---

## 🗃 Database & migrations

SQLite is the only backend shipped, but every repository implements an
interface in `src/databases/interfaces/` so the bot never imports SQLAlchemy
directly. Primary keys are UUID v7 strings (36 chars) — monotonic timestamps
embedded in the high bits keep B-tree inserts fast while still being globally
unique.

```bash
# create a new revision after editing a model
alembic revision --autogenerate -m "add foo column"

# apply
alembic upgrade head

# rollback
alembic downgrade -1
```

`alembic/env.py` reads the URL from `settings.database_url_sync`, so it works
from both CLI and the k3s `migrate-job.yaml`. The revision chain is dual-dialect:
`0001_initial_schema` creates the SQLite schema and is a no-op on Postgres;
`0002_postgres_parity` creates the Postgres-native schema (UUID /
TIMESTAMPTZ / BOOLEAN + `uq_kv_store_owner_key`) and is a no-op on SQLite.
One `alembic upgrade head` works against either backend — no branches,
no dialect flags.

---

## 📨 Queues

```
┌─────────┐  enqueue  ┌──────────────┐  compute  ┌────────────┐  enqueue  ┌──────────────────┐  rate-limited  ┌──────────┐
│  bot    ├──────────▶│ tasks_queue   ├──────────▶│  worker    ├──────────▶│ delivery_queue   ├───────────────▶│ Telegram │
│  (PTB)  │           │  (RabbitMQ)   │           │ processing │           │  (RabbitMQ)      │                │   API    │
└─────────┘           └──────────────┘           └────────────┘           └──────────────────┘                └──────────┘
```

The delivery worker enforces two rolling-second counters in Redis:

* `DELIVERY_RATE_PER_SECOND` — global (Telegram's ≈30 msg/s ceiling).
* `DELIVERY_RATE_PER_CHAT`   — per chat (Telegram's ≈1 msg/s/chat ceiling).

When a budget is exhausted, the task sleeps briefly and re-checks; if no slot
frees up within 5 s, the task is re-queued with exponential backoff.

### Sending messages: generic task + facade

`src/tasks/delivery.py` exposes **one Celery task** — `deliver(method, payload)` —
and a handful of ergonomic wrappers on top of it.

```python
from src.tasks.delivery import send_text, send_photo, edit_text, deliver

# Text with inline buttons
send_text(
    chat_id,
    "Pick one:",
    reply_markup={"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]},
)

# Photo by URL or file_id, with caption
send_photo(chat_id, "https://example.com/cat.jpg", caption="<b>cat</b>")

# Edit an existing message
edit_text(chat_id, message_id, "updated")

# Anything the facade doesn't cover — call the generic task directly
deliver.delay(method="sendMediaGroup", payload={
    "chat_id": chat_id,
    "media": [{"type": "photo", "media": "file_id_1"}, ...],
})
deliver.delay(method="answerCallbackQuery", payload={
    "callback_query_id": cbq_id, "text": "done"})
```

Why this shape:

* **One task**, one place to own rate-limiting, retries, 429 handling and
  Bot API error parsing. Adding a new Bot API call does not require a new
  Celery task or new routing.
* The **facade** (`send_text` / `send_photo` / `edit_text`) exists purely
  for ergonomics — callers shouldn't have to remember method strings or
  build payloads by hand. Add helpers freely; keep them one-liners over
  `deliver.delay(...)`.
* Chat-less Bot API methods (`answerCallbackQuery`, `answerInlineQuery`)
  skip the per-chat budget automatically — only the global budget applies.
* Raw file uploads (`multipart/form-data`) are intentionally out of scope.
  The Right Way with Telegram is to upload a file once and reuse its
  `file_id` — which travels through the standard JSON payload.

---

## 🎛 Polling vs Webhook vs Receiver

Switch via `MODE`:

```bash
# development
MODE=polling python main.py

# production — bot owns the webhook (v2 topology)
MODE=webhook \
WEBHOOK_BASE_URL=https://example.f8f.dev \
WEBHOOK_SECRET_TOKEN=... \
python main.py

# v3 Phase 2 — stateless receiver in front of RabbitMQ
MODE=receiver \
WEBHOOK_BASE_URL=https://example.f8f.dev \
WEBHOOK_SECRET_TOKEN=... \
RABBITMQ_URL=amqp://guest:guest@localhost:5672// \
UPDATES_QUEUE=updates_queue \
python main.py
```

`polling` and `webhook` boot PTB via `src/bot/runner.py`; `receiver`
boots the FastAPI app in `src/receiver/runner.py` — no PTB, no DB, no
LLM. Only one endpoint per bot token is ever registered with Telegram,
so `webhook` and `receiver` are mutually exclusive in production.

---

## ☸️ k3s deployment

```bash
# 1. Build & push / load your image so the cluster can pull it.
docker build -t tg-business-bot:latest .
docker save tg-business-bot:latest | sudo k3s ctr images import -

# 2. Create the secret (do NOT commit the filled-in file).
cp k8s/base/secret.yaml.example k8s/base/secret.yaml
$EDITOR k8s/base/secret.yaml

# 3. Pick a mode and apply via kustomize.
kubectl apply -k k8s/overlays/polling     # polling
#  — or —
kubectl apply -k k8s/overlays/webhook     # webhook (bot owns the URL)
#  — or (v3 Phase 2) —
kubectl apply -k k8s/overlays/scaled      # stateless receiver + HPA

# 4. Run migrations (idempotent).
kubectl -n tg-bot delete job tg-bot-migrate --ignore-not-found
kubectl -n tg-bot apply -k k8s/overlays/polling     # re-creates the Job
```

The webhook overlay ships a Traefik Ingress for `example.f8f.dev` — change it
in both `k8s/base/configmap.yaml` (via the kustomize overlay `WEBHOOK_BASE_URL`
literal) and `k8s/overlays/webhook/bot-ingress.yaml`. The base domain could
also live in a Secret if you prefer; by default we keep it in the ConfigMap
since it's not sensitive — only the bot token / webhook secret token are.

---

## 💾 Backups

### In-cluster (automatic)

`k8s/base/backup-cronjobs.yaml` defines two CronJobs:

* **`backup-sqlite`** — `02:15 UTC` daily. Uses `sqlite3 .backup` (consistent
  hot-copy), gzips the output into the `tg-bot-backups` PVC, keeps the last
  14 snapshots.
* **`backup-rabbitmq`** — `02:30 UTC` daily. Calls `GET /api/definitions` on
  the RabbitMQ Management plugin (exchanges, queues, bindings, users,
  policies, vhosts) and stores the gzipped JSON. 30-day retention.

Mount the `tg-bot-backups` PVC read-only into any rsync / restic sidecar to
ship the files off-cluster.

### Local / ad-hoc

```bash
./scripts/backup_sqlite.sh                    # backups/sqlite-<ts>.db.gz
RABBITMQ_MGMT_URL=http://localhost:15672 \
    ./scripts/backup_rabbitmq.sh              # backups/rabbitmq-<ts>.json.gz
```

### Full RabbitMQ message backup

Message-level backup is intentionally out of scope for the CronJob — use the
Shovel plugin (replicate critical queues to a secondary broker) or snapshot
the `rabbitmq-data` PVC with your storage driver's volume-snapshot feature.

---

## ⚙️ Configuration reference

All options live in `src/config/settings.py`. The full `.env.example` is in
the repo root. The most important knobs:

| Variable                    | Default                               | Meaning                                         |
| --------------------------- | ------------------------------------- | ----------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`        | —                                     | Bot token from @BotFather                       |
| `MODE`                      | `polling`                             | `polling`, `webhook`, or `receiver` (v3 Phase 2)|
| `WEBHOOK_BASE_URL`          | `https://example.f8f.dev`             | Public HTTPS URL (no trailing slash)            |
| `WEBHOOK_PORT`              | `8080`                                | Port the bot binds to                           |
| `WEBHOOK_SECRET_TOKEN`      | —                                     | Secret header Telegram sends with each webhook  |
| `DEFAULT_LOCALE`            | `en`                                  | Fallback when user's `language_code` is unknown |
| `DATABASE_BACKEND`          | `sqlite`                              | `sqlite` (default, local dev) or `postgres` (v3 Phase 4) |
| `DATABASE_PATH`             | `data/bot.db`                         | SQLite file (`DATABASE_BACKEND=sqlite` only)    |
| `POSTGRES_DSN`              | `postgresql://bot:bot@localhost:5432/bot` | Postgres URL used when `DATABASE_BACKEND=postgres`. `postgres://` and `postgresql://` both accepted |
| `RABBITMQ_URL`              | `amqp://guest:guest@localhost:5672//` | Celery broker                                   |
| `QUEUE_TASKS`               | `tasks_queue`                         | Processing queue name                           |
| `QUEUE_DELIVERY`            | `delivery_queue`                      | Rate-limited sending queue                      |
| `QUEUE_DELIVERY_DLQ`        | `delivery_dlq`                        | Dead-letter queue for terminal delivery failures|
| `UPDATES_EXCHANGE`          | `""`                                  | Receiver publish exchange. `""` = Phase-2 default; `updates` = Phase-3 x-consistent-hash |
| `UPDATES_QUEUE`             | `updates_queue`                       | Receiver publish queue (Phase 2 single shard, ignored when `UPDATES_EXCHANGE` is set) |
| `UPDATES_SHARDS`            | `16`                                  | Phase-3 shard count (must equal `update-consumer` replicas) |
| `DEDUP_TTL_SECONDS`         | `3600`                                | How long an `update_id` stays claimed in Redis  |
| `REDIS_URL`                 | `redis://localhost:6379/0`            | Cache + Celery result backend                   |
| `REDIS_SAVE_TTL`            | `3600`                                | `/redis_save` expiry (s). 0 = forever           |
| `DELIVERY_RATE_PER_SECOND`  | `25`                                  | Global send budget (Telegram: ~30/s)            |
| `DELIVERY_RATE_PER_CHAT`    | `1`                                   | Per-chat budget (Telegram: ~1/s)                |

---

## 🧪 Tests

The suite covers the foundation: pure logic, persistence, Redis cache and the
Alembic upgrade chain. PTB handlers and Celery tasks intentionally stay
uncovered for now — they get tested alongside the first real feature. Anything
in `src/bot/runner.py` and `main.py` is excluded from coverage in
`pyproject.toml` (boot wiring).

### Install & run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Run the whole suite (quiet, fail-fast on warnings)
pytest

# Or with coverage report
pytest --cov

# Subset
pytest tests/unit                                       # fast, no I/O
pytest tests/integration                                # in-memory SQLite + fakeredis + alembic
pytest tests/integration/test_alembic_migrations.py     # single file
pytest -k "test_pick_locale"                            # by name
```

The runner uses `pyproject.toml [tool.pytest.ini_options]`:
* `asyncio_mode = "auto"` — `async def test_*` functions are awaited automatically.
* `filterwarnings = ["error", …]` — most warnings are errors; only fluent-runtime's
  ResourceWarning and a handful of upstream deprecations are ignored.
* `pythonpath = ["."]` — so `src.*` imports resolve without an editable install.

### What's tested

| Layer                         | File                                                          | Notes                                               |
| ----------------------------- | ------------------------------------------------------------- | --------------------------------------------------- |
| UUID v7 generator             | `tests/unit/test_uuid7.py`                                    | version bits, monotonicity, fallback path           |
| Pydantic Settings             | `tests/unit/test_settings.py`                                 | validators, derived URLs, webhook constraint        |
| i18n translator               | `tests/unit/test_translator.py`                               | locale picker, gettext, fallbacks                   |
| Bot DTO converter             | `tests/unit/test_bot_utils.py`                                | `dto_from_telegram_user`                            |
| User repository (SQLite)      | `tests/integration/test_sqlite_user_repository.py`            | upsert, get, set_language, language preservation    |
| Business-conn repo (SQLite)   | `tests/integration/test_sqlite_business_repository.py`        | upsert/get/set_enabled/delete                       |
| Message-mapping repo (SQLite) | `tests/integration/test_sqlite_message_mapping_repository.py` | add + get_by_notification_id                        |
| KV-store repo (SQLite)        | `tests/integration/test_sqlite_kv_repository.py`              | set/get/delete + isolation by owner                 |
| Session rollback              | `tests/integration/test_session_rollback.py`                  | exception inside session must roll back             |
| Redis cache                   | `tests/integration/test_redis_cache.py`                       | save/read, wait flag, TTL semantics (via fakeredis) |
| Alembic migrations            | `tests/integration/test_alembic_migrations.py`                | SQLite live + Postgres offline-SQL (UUID/TIMESTAMPTZ) + opt-in live PG |
| SQLite→Postgres migrate       | `tests/unit/test_migrate_script.py` + `tests/integration/test_migrate_script.py` | `coerce_row`, URL validators, `_batched`, `_redact`, argparse; URL rejection + opt-in live PG copy/refusal |
| Postgres→SQLite rollback      | `tests/unit/test_migrate_postgres_to_sqlite.py` + `tests/integration/test_migrate_postgres_to_sqlite_script.py` | inverted `coerce_row` (UUID→CHAR(36)), bool/tz pass-through, drift-check against forward script + SQLite `Base.metadata`; opt-in live PG round-trip |
| Delivery facade               | `tests/unit/test_delivery_facade.py`                          | `send_text` / `send_photo` / `edit_text` payloads   |
| Delivery task                 | `tests/integration/test_delivery_task.py`                     | routing, rate-limit, 429 → Retry, 5xx, DLQ, metrics |
| Update dedup                  | `tests/integration/test_idempotency.py`                       | first-call wins, TTL, non-destructive `has_seen`    |
| Dedup PTB wiring              | `tests/integration/test_dedup_handler.py`                     | `ApplicationHandlerStop` on duplicate `update_id`   |
| Prometheus metrics            | `tests/unit/test_metrics.py`                                  | metric types, label names, custom histogram buckets |
| Receiver /metrics + counters  | `tests/integration/test_receiver_metrics.py`                  | `/metrics` format, counter increments (published/dedup/error), histogram observation |

### What's NOT tested yet (lands with the first real feature)

* PTB handlers (`/start`, `/smoke`, `/redis_*`, business, errors)
* Celery `processing.smoke` (the blocking sleep + chain-to-delivery)
* End-to-end smoke (Docker Compose / kubectl kustomize)

---

## 📜 License

Internal project. See repository metadata for licensing.
