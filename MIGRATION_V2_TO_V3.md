# Migration plan: v2 → v3

Target: horizontally-scalable Telegram bot. v2 already has the queue
primitives (RabbitMQ, Celery, `worker-tasks`, `worker-delivery`,
rate-limited `deliver`). v3 finishes the job by decoupling **update
ingestion** from **handler execution**, sharding by `chat_id` to keep
ordering, and removing single-replica bottlenecks on state.

This document is the plan only — **no code changes in this commit**.
Each phase below is a separate PR/commit.

---

## 0. Guiding principles

* **Handler code stays untouched.** All v2 handlers keep working; the bus
  around them changes.
* **Every phase is deployable on its own.** No big-bang cutover.
* **Nothing new is written before tests exist for it.** Follow v2's
  coverage philosophy — unit + integration, `pytest`-only.
* **No new hard dependencies** unless they replace something. Stay on
  RabbitMQ/Celery/Redis/SQLite→Postgres.

---

## 1. Current state (v2, inherited into v3)

```
Telegram ──▶ bot Deployment (1 replica) ─┬──▶ tasks_queue ─▶ worker-tasks
             [ webhook server (PTB)      │
               + handlers                 └──▶ delivery_queue ─▶ worker-delivery ──▶ Telegram
               + Celery producer ]
```

Pain points:
1. Webhook receiver + handlers + Celery producer all in one process.
2. `bot` Deployment pinned to `replicas: 1`.
3. SQLite on RWO PVC — blocks multi-replica handlers.
4. No `update_id` dedup; retries from Telegram are processed twice.
5. `deliver` rate-limiter is best-effort; no 429 `retry_after`, no DLQ.
6. No per-chat ordering guarantee once handlers run on multiple replicas.

---

## 2. Target state (v3)

```
Telegram ─HTTPS─▶ Ingress
                    │
                    ▼
          webhook-receiver (Deployment, HPA on RPS/CPU, N replicas)
             · validates secret_token
             · SETNX update:{id}  (dedup, TTL 1h)
             · publishes to "updates" exchange (x-consistent-hash, key=chat_id)
             · returns 200 in <50ms
                    │
                    ▼
          RabbitMQ "updates" exchange  ──fanout by hash──▶  updates.shard.{0..N-1}
                                                                   │
                          (one consumer-in-flight per shard)       ▼
                                                            worker-tasks
                                                             · reconstructs Update
                                                             · runs PTB handlers
                                                             · publishes to delivery_queue
                    ▼
          delivery_queue ─▶ worker-delivery
                            · global token-bucket (Redis)
                            · per-chat token-bucket (Redis)
                            · honors 429 retry_after
                            · DLQ after N retries
                    ▼
               Telegram Bot API
```

State:
* **DB** → Postgres (managed or StatefulSet). SQLite stays as a dev
  backend.
* **Redis** → unchanged, reused for dedup + buckets.
* **RabbitMQ** → unchanged, one new exchange + shard queues.

---

## 3. Phased migration

Each phase ends with a green test suite and a deployable image. Later
phases can be cherry-picked independently if priorities shift.

### Phase 1 — Idempotency & rate-limit hardening (safety net) ✅ shipped

Goal: fix correctness before changing topology.

Delivered:
* `src/cache/idempotency.py`: async `claim_update` via `SET NX EX`,
  configurable TTL (`DEDUP_TTL_SECONDS`, default 3600s).
* `src/bot/handlers/dedup.py` + `TypeHandler(Update, …)` registered in
  group `-1` of `build_application` — duplicate `update_id` raises
  `ApplicationHandlerStop` before any real handler runs.
* `src/tasks/delivery.py`:
  * 429 handling reads `retry_after` from both top-level and
    `parameters.retry_after`; picks the larger.
  * 5xx → plain `RuntimeError`, `autoretry_for=(Exception,)` does
    exponential backoff with jitter.
  * `_DeliveryTask` base with `on_failure` hook publishes a raw JSON
    record (`method`, `payload`, `reason`, `task_id`, `traceback`, `ts`)
    to `delivery_dlq` via the Celery producer pool (`throws=(Retry,)`
    prevents Retry from triggering DLQ).
  * `delivery_dlq` declared alongside the other queues in
    `celery_app.conf.task_queues` (no consumer — ops drain manually).
* `src/tasks/metrics.py`: counters
  `deliver_sent_total{method}`, `deliver_throttled_total{method}`,
  `deliver_server_error_total{method}`,
  `deliver_retried_total{method,reason}`,
  `deliver_dead_lettered_total{method,reason}`,
  `dedup_hit_total`, `dedup_miss_total`.
  In-process only — HTTP `/metrics` exposition deferred to Phase 5.
* `prometheus-client>=0.21.0` added to `requirements.txt`.
* Tests (`tests/integration/test_idempotency.py`,
  `test_dedup_handler.py`, extended `test_delivery_task.py`):
  dedup hit/miss/TTL/string-vs-int keys; `ApplicationHandlerStop` on
  duplicate; 429 layouts; 5xx retry path; `on_failure` DLQ publish +
  metric; DLQ failure doesn't mask the original exception. 98 tests
  green, coverage 77.9%.

Deployable: still single `bot` replica, but no duplicate work and no
silent failures.

### Phase 2 — Extract `webhook-receiver` ✅ shipped

Goal: decouple ingestion from handlers. Still one worker fleet, but the
hot path is now trivial.

**Shipped:**

* New package `src/receiver/`:
  * `app.py` — FastAPI app with one route: `POST /{bot_token}` (path
    derived from the token so each bot gets a distinct URL).
  * Validates `X-Telegram-Bot-Api-Secret-Token`.
  * Reuses the Phase-1 dedup helper via `claim_update()`.
  * Publishes raw JSON to the broker with `chat_id` in headers (routing
    key plumbed for Phase 3's `x-consistent-hash` exchange).
  * `/healthz` (liveness) + `/readyz` (Redis + broker reachable).
* `publisher.py` — async aio-pika publisher with `connect_robust()` and
  publisher confirms. Declares `updates_queue` as durable. Phase 3 will
  swap `exchange=""` for the consistent-hash exchange; call sites are
  unchanged.
* `chat_id.py` — best-effort `chat_id` extraction across all update
  variants (message, edited_*, channel_post, business_*, callback_query,
  business_connection, inline_query, …).
* `runner.py` — uvicorn bootstrap for `MODE=receiver`.
* `main.py` — branches on `settings.mode == "receiver"` (polling /
  webhook / receiver). One image, different command.
* `src/config/settings.py` — new env vars: `UPDATES_EXCHANGE` (default
  `""` → default direct exchange in Phase 2), `UPDATES_QUEUE`
  (`updates_queue`), extended `MODE` Literal with `receiver`.
* `requirements.txt` — `fastapi`, `uvicorn[standard]`, `aio-pika`.
* k8s: new overlay `k8s/overlays/scaled/` with `webhook-receiver`
  Deployment (2 replicas baseline), Service, Ingress, HPA (2–10 replicas
  on 70 % CPU). `MODE=receiver` and `UPDATES_QUEUE` pinned in the
  overlay ConfigMap.
* Tests: 24 new tests in `tests/integration/test_receiver.py` covering
  happy path, bad secret → 401, missing secret → 401, empty-secret
  bypass, invalid JSON → 400, missing `update_id` → 400, duplicate →
  200 without publish, publisher error → 503, update without chat →
  published with `chat_id=None`, `/healthz`, `/readyz` (ok / broker
  disconnected / Redis down), and a parametrized `extract_chat_id`
  matrix.

Deployable: `webhook-receiver` fronts Telegram behind the public
Ingress; publishes to RabbitMQ. Phase 3 wires the sharded consumer that
replaces the single `bot` Deployment. In the meantime, the `scaled`
overlay is suitable for staging validation of the producer side — keep
running the `webhook` overlay in production until Phase 3 lands.

### Phase 3 — Consistent-hash sharding + handler worker ✅ shipped

Goal: run PTB handlers on N replicas without losing per-chat ordering.

**Shipped:**

* `k8s/base/rabbitmq.yaml` — enabled `rabbitmq_consistent_hash_exchange`
  via a ConfigMap-backed `/etc/rabbitmq/enabled_plugins` mount.
* `src/tasks/broker_topology.py` — idempotent helper that declares the
  `updates` exchange (`x-consistent-hash`, durable) and N shard queues
  `updates.shard.0`..`updates.shard.{N-1}`, each bound with weight `"1"`
  (uniform distribution across the hash ring). Falls back to single-queue
  Phase-2 mode when `UPDATES_EXCHANGE=""`.
* `src/config/settings.py` — new `UPDATES_SHARDS` (default 16, bounded
  `[1, 256]`) and `shard_queue_name(i)` helper.
* `src/receiver/publisher.py` — uses the topology helper; publishes with
  `routing_key=str(chat_id)` in Phase-3 mode and `"0"` for chat-less
  updates. `chat_id` travels in headers for Phase-5 observability.
* `src/tasks/update_consumer.py` — process-wide PTB `Application` built
  once per worker fork in `worker_process_init`, driven by a dedicated
  daemon-thread event loop so Celery's sync tasks can dispatch async
  handlers via `run_coroutine_threadsafe`. Shared handler wiring with
  `build_application()` — zero drift between polling / webhook /
  receiver paths.
* `src/tasks/processing.py` — new `handle_update(raw_update)` Celery
  task (`acks_late=True`, autoretry × 3, jittered backoff) that forwards
  to `dispatch_update`.
* `src/tasks/celery_app.py` — dynamically declares shard queues from
  `UPDATES_SHARDS`; `worker_process_init` hooks gated on `-Q
  updates.shard.` in argv so delivery/tasks workers stay PTB-free.
* k8s: `k8s/overlays/scaled/update-consumer.yaml` — StatefulSet (pod
  ordinal → shard index), `--concurrency=1 --prefetch-multiplier=1` per
  pod to preserve per-chat ordering. Overlay ConfigMap pins
  `UPDATES_EXCHANGE=updates` and `UPDATES_SHARDS=4`.
* Tests: 24 new (146 total) — settings bounds and `shard_queue_name`,
  `declare_updates_topology` (Phase-2 fallback, Phase-3 shape, weight
  uniformity, single-shard edge case), publisher routing (Phase-2 vs
  Phase-3 exchange selection, `chat_id` → routing_key mapping,
  chat-less → "0" fallback, headers, broker-failure propagation),
  `dispatch_update` (uninitialised worker guard, PTB feed-through,
  `de_json=None` swallow, exception propagation for DLQ), and
  `handle_update` task body.

Deployable: handlers now scale horizontally; per-chat ordering preserved
by `x-consistent-hash` + single-consumer shards. Replaces the old
single-replica `bot` Deployment once cutover is done.

### Phase 4 — SQLite → PostgreSQL migration

Goal: drop the single-replica constraint tied to SQLite's RWO PVC and
make the handler fleet truly horizontal. Split into four sub-phases so
the backend swap and the data copy are independently reversible.

#### 4.1 Postgres backend code

* New package `src/databases/postgres/`, mirroring `src/databases/sqlite/`:
  * `database.py` (same name as in `src/databases/sqlite/`) — async
    engine/session factory via
    `sqlalchemy.ext.asyncio` + `asyncpg`; sync engine via `psycopg` for
    Alembic.
  * `models.py` — same tables, Postgres-native types:
    * UUID v7 → `UUID` (not `text(36)`). Store as native `uuid`; the
      v7 bits still give monotonic inserts on a btree.
    * Timestamps → `TIMESTAMPTZ` (SQLite was naive ISO text).
    * Booleans → `BOOLEAN` (SQLite was `INTEGER 0/1`).
    * JSON payloads (if any land later) → `JSONB`.
  * `repositories/` — implement every `I*Repository` from
    `src/databases/interfaces/`. Use `INSERT ... ON CONFLICT` for the
    upsert paths (SQLite used `INSERT OR REPLACE`).
* Register in `src/databases/factory.py`:
  `DATABASE_BACKEND=postgres` → return the Postgres implementation.
  SQLite stays the default for local dev.
* Config: new `DATABASE_URL` env is already the single knob. Derived
  `database_url_sync` / `database_url_async` in `settings.py` adjusted
  to produce `postgresql+psycopg://` and `postgresql+asyncpg://`.
* Dependencies: add `asyncpg` and `psycopg[binary]` to
  `requirements.txt`; `testcontainers[postgres]` to
  `requirements-dev.txt`.

**Shipped (4.1):**

* `src/databases/postgres/` — mirror of `src/databases/sqlite/` (same
  module layout, same DTOs):
  * `models/base.py` — `UuidV7PrimaryKeyMixin` uses
    `sqlalchemy.dialects.postgresql.UUID(as_uuid=False)` so the `str`
    DTO shape is dialect-agnostic; `CreatedAtMixin` / `TimestampMixin`
    emit `TIMESTAMPTZ` via `DateTime(timezone=True)`.
  * `models/{user,business_connection,message_mapping,kv_store}.py` —
    native `BOOLEAN`; `kv_store` gains a
    `uq_kv_store_owner_key` unique constraint that the upsert path
    relies on.
  * `repositories/` — single-round-trip upserts via
    `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(constraint=...)`
    + `RETURNING`; read paths unchanged.
  * `database.py` — `create_async_engine` with pooling
    (`pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`); no
    SQLite-only `check_same_thread`.
* `src/databases/factory.py` — `DATABASE_BACKEND=postgres` → returns
  `PostgresDatabase(settings.database_url)`. SQLite stays the default.
* `src/config/settings.py` — `database_backend: Literal["sqlite", "postgres"]`;
  new `POSTGRES_DSN`; `_rewrite_postgres_driver` normalises
  `postgres://` / `postgresql://` / `postgresql+<driver>://` and swaps
  the driver to `asyncpg` (async path) or `psycopg` (Alembic sync path).
  `DATABASE_URL` / `DATABASE_URL_SYNC` are derived from those.
* `requirements.txt` — `asyncpg>=0.30.0`, `psycopg[binary]>=3.2.0`.
* Tests: 21 new (167 total) — DSN rewriter edge cases
  (bare `postgres://`, existing driver suffix, query strings, rejected
  schemes), settings wiring (async→asyncpg, sync→psycopg, sqlite
  unchanged), CREATE TABLE DDL asserts native `UUID` / `TIMESTAMPTZ` /
  `BOOLEAN` types, factory dispatch (sqlite/postgres without
  connecting, unknown backend rejected by pydantic `Literal`).
* Still remaining for 4.5+: k8s overlay for Postgres, backup-postgres
  CronJob, cutover runbook.
* **Follow-up — done (post-4.1):** Postgres primary key is now native
  `UUID(as_uuid=True)` and every DTO exposes `id: uuid.UUID | None`.
  The SQLite backend reuses the existing `CHAR(36)` storage via a
  `UuidAsString36` TypeDecorator (models/base.py) — no Alembic data
  migration required — so the Python layer is fully typed across both
  dialects without touching on-disk bytes. `uuid7()` is the single
  shared default; handlers drop the `id=""` placeholder.

#### 4.2 Alembic: dual-dialect migrations — **Shipped**

Both backends are now driven from the **same** revision chain. One
`alembic upgrade head` works against either dialect; each revision is a
no-op on the "other" dialect, so Alembic's version table stays in lock-step.

* `alembic/versions/0001_initial_schema.py` — guarded with
  `if op.get_bind().dialect.name == "postgresql": return` at the top of
  both `upgrade()` and `downgrade()`. Unchanged for SQLite; no-op on
  Postgres. Preserves the committed v2 SQLite history byte-for-byte.
* `alembic/versions/0002_postgres_parity.py` — the mirror: no-op on
  SQLite, creates the parallel schema on Postgres using dialect-native
  types (`postgresql.UUID(as_uuid=True)`, `TIMESTAMPTZ`, native
  `BOOLEAN`) plus the `uq_kv_store_owner_key` composite unique
  constraint the Phase-4.1 kv-store repo's `ON CONFLICT ON CONSTRAINT`
  upsert relies on.
* `alembic/env.py` — now picks the right declarative `Base` based on the
  active URL's dialect (`from src.databases.postgres.models import Base`
  on Postgres, SQLite otherwise) so `compare_type=True` autogen runs
  against dialect-native metadata. `render_as_batch` is enabled only on
  SQLite (it is a workaround for SQLite's missing `ALTER TABLE`, and
  leaving it on for Postgres would degrade autogen there).
* `migrate-job.yaml` (k8s) stays unchanged — it just runs
  `alembic upgrade head` with the new URL.

Tests (`tests/integration/test_alembic_migrations.py`, 10 assertions,
10 passing):

* SQLite path: live upgrade/downgrade against a throwaway file DB;
  schema matches `SqliteBase.metadata`; idempotent re-upgrade; captured
  offline SQL shows `VARCHAR(36)`/no `UUID`/no `TIMESTAMPTZ`.
* Postgres path: Alembic `--sql` offline mode against a dummy
  `postgresql://` URL (no live server needed). Asserts that every
  metadata table gets `CREATE TABLE`, every PK is `UUID NOT NULL`, all
  timestamps are `TIMESTAMP WITH TIME ZONE`, the
  `uq_kv_store_owner_key` constraint is emitted, and both revision rows
  land in `alembic_version`. A downgrade-from-`0002` path asserts all
  four tables are dropped.
* Opt-in live-Postgres test (`POSTGRES_TEST_URL=…` in the environment):
  real upgrade/idempotent-re-upgrade/downgrade-to-base against a running
  Postgres. Skipped by default so the suite stays hermetic; CI wires it
  up with a Postgres service container.

Policy going forward: every new migration is tested against **both**
dialects in CI (live SQLite + live Postgres service; offline SQL diff
against the postgres dialect as a hermetic lower bound in unit tests).

#### 4.3 Data migration (one-shot, v2 SQLite → v3 Postgres) — **Shipped**

Assumption: production has a small dataset (hundreds of users, tens of
business connections, bounded KV entries). A streaming bulk copy under
a short maintenance window beats dual-write machinery.

Codified as `scripts/migrate_sqlite_to_postgres.py`; drive with
`python -m scripts.migrate_sqlite_to_postgres --source sqlite:///data/bot.db
--target postgresql+psycopg://bot:pw@host/bot`. What the script does:

* Validates the URL pair (rejects non-sqlite source, non-postgresql target).
* Runs `alembic upgrade head` against the target so the dual-dialect
  chain from Phase 4.2 lands `users` / `business_connections` /
  `message_mappings` / `kv_store` with native UUID / TIMESTAMPTZ /
  BOOLEAN + the `uq_kv_store_owner_key` constraint.
* Preflights: refuses to run if any row-bearing table has rows. Safety
  net against accidental re-runs clobbering live data.
* Copies each table in FK-safe order with batched
  `INSERT … ON CONFLICT DO NOTHING`. Rows are coerced en route:
  `id` str→`uuid.UUID`, `is_enabled` int→bool, naive
  `created_at`/`updated_at` → tz-aware UTC.
* Verifies row counts per table post-copy; raises on mismatch.
* Writes a JSON report to `--report` (default
  `backups/migration-<utc-ts>.json`) with per-table
  source/target/duration plus a credential-stripped URL pair.

What the script does **not** do — all operator steps in the runbook
below:

* Freeze writes / scale pods.
* Snapshot the source (`sqlite3 src.db ".backup snapshot.db"`).
* Flip `DATABASE_BACKEND=postgres` + restart workers.

Tests (`tests/integration/test_migrate_script.py`, 2 unconditional + 4
opt-in): URL validation rejects before any connection; live-PG happy
path copies every row; spot-check asserts UUID/tz/bool coercion worked
end-to-end on the target; non-empty target is refused; the CLI
(`main([...])`) writes a well-formed JSON report with credentials
redacted. Unit helpers (34 tests,
`tests/unit/test_migrate_script.py`) cover the pure functions:
URL validators, `coerce_row`, `_batched`, `verify_row_counts`, argparse,
`_redact`, and `TABLES_IN_ORDER` drift against `Base.metadata`.

**Cutover runbook** (operator-driven; keep alongside this section):

1. **Freeze writes.**
   * Scale `bot` / `worker-tasks` / `worker-delivery` to 0 replicas.
   * Leave `webhook-receiver` up *if* Phase 2 already shipped: the
     receiver keeps returning 200 and updates pile up in RabbitMQ —
     zero user-visible downtime beyond the write freeze.
   * Otherwise: put Telegram into a 503 window by unsetting the webhook
     or pointing it at a static page.

2. **Snapshot source.**
   * `sqlite3 source.db ".backup snapshot.db"` (consistent hot copy;
     `scripts/backup_sqlite.sh` wraps this).
   * Work off `snapshot.db` from here on; keep the original untouched
     until verified (rollback anchor).

3. **Run the migration script.** The script performs the URL check,
   `alembic upgrade head`, empty-target preflight, per-table copy with
   `ON CONFLICT DO NOTHING`, and row-count verification, then writes a
   JSON report:
   ```
   python -m scripts.migrate_sqlite_to_postgres \
       --source sqlite:///snapshot.db \
       --target "$POSTGRES_DSN_SYNC"
   ```
   A non-zero exit code means verify / copy failed; inspect the report
   and rerun after truncating the target.

4. **Flip config.**
   * `DATABASE_BACKEND=postgres`, `POSTGRES_DSN=postgresql://…` in the
     ConfigMap/Secret.
   * `kubectl rollout restart deployment/worker-tasks
     deployment/worker-delivery statefulset/update-consumer
     deployment/webhook-receiver`.
   * Scale back to normal replica counts.
   * Queued updates from the maintenance window drain through.

5. **Post-migration soak (24h).**
   * Keep SQLite PVC + snapshot for 7 days.
   * Monitor: handler error rate, `delivery_queue` depth, Postgres
     connections, slow-query log.

#### 4.4 Rollback plan — **Shipped**

Keep the SQLite PVC + Phase-0 snapshot for **7 days** post-cutover.
Three tiers, in order of preference:

##### Tier 1 — Config rollback (< 2 min, lossy)

Use when: a problem shows up within minutes of cutover and the volume
of post-cutover writes is negligible.

1. Flip ConfigMap: `DATABASE_BACKEND=sqlite`, re-attach the SQLite
   volume mount (`DATABASE_PATH=/data/bot.db`).
2. `kubectl rollout restart deployment/worker-tasks
   deployment/worker-delivery statefulset/update-consumer
   deployment/webhook-receiver`.
3. Verify handler error rate + `delivery_queue` depth settle.

Trade-off: writes that landed on Postgres since cutover are lost. Only
acceptable if the Postgres side is confirmed broken and only a handful
of minutes of traffic passed.

##### Tier 2 — Data rollback via reverse script (< 30 min, lossless)

Use when: Postgres is broken and tier 1's write loss is unacceptable.
This tier requires a short maintenance window.

1. **Freeze writes on the Postgres side.** Scale workers to 0:
   ```
   kubectl scale deployment/worker-tasks deployment/worker-delivery \
       deployment/webhook-receiver --replicas=0
   kubectl scale statefulset/update-consumer --replicas=0
   ```
2. **Run the reverse copier** against a fresh SQLite file. The script
   performs URL validation, `alembic upgrade head` on the SQLite
   target, empty-target preflight, per-table copy with `ON CONFLICT
   DO NOTHING`, and row-count verification, then writes a JSON report:
   ```
   python -m scripts.migrate_postgres_to_sqlite \
       --source "$POSTGRES_DSN_SYNC" \
       --target sqlite:///data/bot-rollback.db
   ```
   A non-zero exit code means verification failed; inspect the report
   in `backups/rollback-<ts>.json` and rerun after truncating the
   target.
3. **Flip config.** `DATABASE_BACKEND=sqlite`,
   `DATABASE_PATH=/data/bot-rollback.db`, re-attach the SQLite volume
   mount (rebind the `tg-bot-data` PVC if it was detached).
4. **Restart workers**, scale back to normal replica counts. Queued
   updates drain through.

Properties of the reverse script:

* Symmetric to `migrate_sqlite_to_postgres.py` — same table order,
  same batch mechanics, same report schema.
* `coerce_row` converts Postgres `uuid.UUID` → hyphenated CHAR(36)
  strings on the way out — matches the v2 on-disk format that
  `UuidAsString36` round-trips on read.
* Booleans and timestamps pass through unchanged; SQLAlchemy's SQLite
  `Boolean` and `DateTime` accept native `bool`/`datetime` values.
* Refuses a non-empty target: operator truncates the SQLite file (or
  passes a fresh path) and reruns if a run crashed mid-copy.
* Credentials redacted in the JSON report (`***` for password).

##### Tier 3 — Nuclear: restore Phase-0 SQLite snapshot

Use when: tiers 1 + 2 can't complete (e.g. Postgres is unreadable, or
the reverse script fails on corrupt data). Accept lost writes.

1. Rehydrate the SQLite file from the Phase-0 snapshot (`backups/
   pre-cutover-*.db`).
2. Flip config as in tier 1.
3. Restart workers, scale back up.

All post-cutover writes are gone. Document what was lost from the
Postgres side for manual reconciliation if feasible.

##### After the 7-day soak

* Delete `tg-bot-data` PVC.
* Remove SQLite backend from the default factory path (keep the code
  for local dev and tests).
* Rename `backup-sqlite` CronJob → `backup-postgres` (`pg_dump -Fc`,
  gzip, same PVC + retention window).

#### 4.5 k8s & ops changes

* New `k8s/base/postgres.yaml`:
  * `StatefulSet` with one replica for the self-hosted path (HA is out
    of scope — single Postgres is fine for this bot's load). PVC for
    data, resource requests modest (256Mi–1Gi, 100m–500m CPU).
  * `Service` (ClusterIP) on `5432`.
  * Secret-driven credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`,
    `POSTGRES_DB`).
* `k8s/base/kustomization.yaml`: include the new file.
* Overlay `k8s/overlays/managed-postgres/` (optional): strips the
  StatefulSet + Service, relies on `DATABASE_URL` from a Secret that
  points to a managed provider.
* `k8s/base/backup-cronjobs.yaml`: add `backup-postgres` (`pg_dump
  -Fc`, daily 02:45 UTC, 30-day retention). Keep `backup-sqlite`
  disabled or removed post-soak.
* `worker-tasks` / `worker-delivery`: drop the `tg-bot-data` PVC
  mount; they no longer touch the filesystem DB.
* `migrate-job.yaml`: no change, but ensure it runs against the new
  URL before workers start.

Deployable outcome: `worker-tasks` can run any replica count; SQLite
remains usable for local dev (`DATABASE_BACKEND=sqlite` in `.env`).

### Phase 5 — Autoscaling & observability

Goal: make the system self-adjust and visible.

* HPAs:
  * `webhook-receiver`: CPU + RPS (Prometheus adapter, or just CPU).
  * `worker-tasks`: KEDA `rabbitmq` scaler on sum(queue depth across
    shards). Min = shard count (ordering constraint), max = shard count
    × K — see note below.
  * `worker-delivery`: fixed or KEDA on `delivery_queue` depth, but
    bounded by Telegram's global rate.
* Prometheus metrics (receiver publish rate, dedup hits, shard queue
  depth, handler latency, deliver 429 rate).
* Optional: Grafana dashboard JSON checked into `k8s/base/`.

Ordering note: if `worker-tasks` scales above N (shard count), the extra
replicas can't consume ordered shards without breaking the one-consumer
rule. Two options:
(a) keep `replicas == shards` and raise `UPDATES_SHARDS` to scale (needs
rebalance — see §5);
(b) allow extra replicas to consume *non-ordered* queues (e.g. chat-less
updates: `callback_query` without `message.chat`).

### Phase 6 — Cutover & cleanup

* `setWebhook` → receiver's public URL.
* Delete the old `bot` Deployment/Service/Ingress, polling overlay
  optional to keep for local parity.
* Remove `src/bot/runner.py` webhook branch (polling stays for dev).
* Update `README.md` — drop the "v3 status: in progress" banner, fold
  the scaling section into the main architecture description.

---

## 4. Risk register

| Risk | Mitigation |
|---|---|
| Telegram retries during Phase-2 cutover duplicate work | Phase 1 dedup must ship first |
| Consistent-hash plugin not enabled on existing RabbitMQ | Phase 3 adds it to manifests; rolling restart plan documented below |
| Re-sharding (changing `UPDATES_SHARDS`) temporarily breaks ordering | Document as a maintenance window; drain queues before change |
| Long-running handler holds up a shard | Hard timeout + DLQ on handler failure; heavy work stays on `worker-delivery`/dedicated queues |
| Postgres migration data loss | SQLite snapshot kept 7 days post-cutover; migration script is idempotent with `ON CONFLICT DO NOTHING`; reverse script `migrate_postgres_to_sqlite.py` for soft rollback (see §4.4) |
| Type coercion bug during SQLite→Postgres copy (tz-naive timestamps, UUID-as-text, bool-as-int) | Row-level transform centralized in one function, covered by integration tests that diff every column on random sample |
| Non-empty Postgres accidentally overwritten by rerun | Migration script refuses to run unless target row counts are zero and Alembic head matches |
| Mid-migration crash | Script is resumable: idempotent inserts + per-table progress logged to the migration report JSON |
| `worker-delivery` over-scaling hits Telegram 429 globally | Keep fixed replicas, rely on Redis token-bucket as the choke |
| PTB `Application` not designed to process arbitrary `Update` JSON out-of-band | Prototype Phase-3 handler wiring early; fall back to a minimal dispatcher if needed |

---

## 5. Re-sharding procedure (operational note)

Changing `UPDATES_SHARDS` changes the hash ring → same `chat_id` may map
to a different shard → brief per-chat ordering break.

Documented runbook:
1. Announce maintenance window (seconds-scale).
2. Stop `webhook-receiver` (or point Telegram webhook at a 503 page).
3. Drain all `updates.shard.*` queues (wait until empty).
4. Apply new shard count via kustomize (`UPDATES_SHARDS`, StatefulSet
   replicas).
5. Resume `webhook-receiver`.

For initial sizing, pick `UPDATES_SHARDS` generously (16–32). Worker
replica count equals shard count; CPU is the usual bottleneck well
before shard count is.

---

## 6. Out of scope for v3

* Multi-tenant / multi-bot-token.
* Kafka / NATS migration.
* gRPC between receiver and workers (keep it broker-mediated).
* Message-level RabbitMQ backup (already explicitly out of scope in v2).
* Replacing Celery with a bespoke consumer (Celery + Kombu can drive
  the consistent-hash exchange via `task_queues` with custom bindings).

---

## 7. Deliverables checklist

- [x] Phase 1: dedup + 429 + DLQ + metrics + tests
- [x] Phase 2: `webhook-receiver` + `MODE=receiver` + `scaled` overlay + tests
- [x] Phase 3: consistent-hash exchange + `handle_update` task +
      StatefulSet-per-shard + ordering tests
- [x] Phase 4.1: `src/databases/postgres/` backend + factory registration + deps
- [x] Phase 4.1 follow-up: native `uuid.UUID` in DTOs + Postgres `UUID(as_uuid=True)` + SQLite `UuidAsString36` TypeDecorator
- [x] Phase 4.2: dual-dialect Alembic migration (`0002_postgres_parity`) + dialect-aware `env.py` + offline-SQL + opt-in live-Postgres tests
- [x] Phase 4.3: `scripts/migrate_sqlite_to_postgres.py` + unit tests (coerce_row, validators, argparse, redact) + opt-in live-Postgres integration tests (fixture copy, non-empty refusal, JSON report via CLI)
- [x] Phase 4.4: reverse script + documented rollback runbook
- [ ] Phase 4.5: `k8s/base/postgres.yaml` + `managed-postgres` overlay + `backup-postgres` CronJob; drop `tg-bot-data` PVC mount from workers
- [ ] Phase 5: HPAs + KEDA + Prometheus metrics + dashboard
- [ ] Phase 6: cutover, cleanup, README consolidation
