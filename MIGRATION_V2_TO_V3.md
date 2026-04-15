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

### Phase 2 — Extract `webhook-receiver`

Goal: decouple ingestion from handlers. Still one worker fleet, but the
hot path is now trivial.

* New package `src/receiver/`:
  * `app.py` — FastAPI (or aiohttp) app with one route: `POST /webhook/{secret_path}`.
  * Validates `X-Telegram-Bot-Api-Secret-Token`.
  * Uses the Phase-1 dedup helper.
  * Publishes raw JSON to `updates` exchange with `routing_key=str(chat_id)`.
  * `/healthz` + `/readyz`.
* `main.py`: add `MODE=receiver` branch (polling / webhook / receiver).
  Kept in one image — the k8s Deployment just overrides the command.
* `src/tasks/receiver_publish.py`: thin wrapper around aio-pika /
  kombu producer. Reused by PTB-in-process path too (so Phase 2 can
  ship even if Phase 3 isn't live).
* k8s: new overlay `k8s/overlays/scaled/` (copy `webhook/` + add
  `webhook-receiver` Deployment, Service, HPA).
* Tests: receiver happy path, bad secret → 401, duplicate → 200 no
  publish, publish failure → 503.

Deployable: `webhook-receiver` can front Telegram; handlers still run in
the single `bot` pod consuming from the shard queues (Phase 3 makes this
sharded, for now it's a single queue). At this point `bot` Deployment
loses its Ingress — Telegram talks only to the receiver.

### Phase 3 — Consistent-hash sharding + handler worker

Goal: run PTB handlers on N replicas without losing per-chat ordering.

* Enable RabbitMQ `rabbitmq_consistent_hash_exchange` plugin (add to
  `k8s/base/rabbitmq.yaml` `enabled_plugins`).
* Declare exchange + shards in `src/tasks/celery_app.py`:
  * `updates` exchange, type `x-consistent-hash`.
  * N bindings to `updates.shard.0` … `updates.shard.{N-1}`, each
    binding's routing key = weight (e.g. `"1"`).
  * Shard count via config `UPDATES_SHARDS` (default 16).
* New Celery task `handle_update(raw_update: dict)` in
  `src/tasks/processing.py`:
  * Rebuilds `telegram.Update.de_json(...)` using a shared `Bot`.
  * Feeds it to a long-lived PTB `Application` built in worker
    `worker_process_init` (one per worker process). Handlers run as
    usual.
  * Crucial: each shard queue must be consumed with
    `prefetch_count=1` and Celery `worker_concurrency=1` **per shard**
    (i.e. one worker pod per shard, or route shards by `-Q` to dedicated
    processes). This is what preserves ordering.
* Deployment topology:
  * `worker-tasks` becomes a parameterized Deployment set, e.g. a
    `StatefulSet` of N replicas each consuming exactly one shard
    (`-Q updates.shard.$(POD_INDEX)`), or a Deployment per shard via
    kustomize generator. StatefulSet is simpler.
* Remove Celery producer code from `bot` process — it's dead code after
  Phase 2.
* Tests: consistent hashing reproducibility (same `chat_id` → same
  shard); ordering property test (1000 updates one chat → in order at
  consumer); handler wiring smoke test.

Deployable: handlers now scale horizontally; per-chat ordering preserved.

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

#### 4.2 Alembic: dual-dialect migrations

The existing `alembic/versions/0001_initial_schema.py` was written for
SQLite. Options, from cleanest to pragmatic:

* **Preferred: replay, don't translate.** Keep 0001 as-is for SQLite
  history, add `0002_postgres_parity.py` that is a no-op on SQLite and
  creates the parallel schema on Postgres. Drives both backends from
  one `alembic upgrade head`.

  Inside the migration:
  ```python
  if context.get_bind().dialect.name == "postgresql":
      # CREATE TABLE ... with UUID / TIMESTAMPTZ
  else:
      # pass — SQLite already has the tables from 0001
  ```

* **Alternative: separate version tables.** Two Alembic branches
  (`--rev-id` prefix per dialect). More moving parts; avoid unless the
  schemas genuinely diverge.

Policy going forward: every new migration is tested against **both**
dialects in CI (testcontainers Postgres + in-memory SQLite).

* `alembic/env.py` stays unchanged — it already reads
  `settings.database_url_sync`.
* `migrate-job.yaml` (k8s) stays unchanged — it just runs
  `alembic upgrade head` with the new URL.

#### 4.3 Data migration (one-shot, v2 SQLite → v3 Postgres)

Assumption: production has a small dataset (hundreds of users, tens of
business connections, bounded KV entries). A streaming bulk copy under
a short maintenance window beats dual-write machinery.

Procedure, codified as `scripts/migrate_sqlite_to_postgres.py`:

1. **Preflight.**
   * Verify source `sqlite://…` and target `postgresql://…` URLs.
   * `alembic upgrade head` against Postgres (empty schema → fully
     migrated schema).
   * Assert row counts on target are zero for every table — refuse to
     run against a non-empty Postgres.

2. **Freeze writes.**
   * Scale `bot` / `worker-tasks` / `worker-delivery` to 0 replicas.
   * Leave `webhook-receiver` up *if* Phase 2 already shipped: the
     receiver keeps returning 200 and updates pile up in RabbitMQ —
     zero user-visible downtime beyond the write freeze.
   * Otherwise: put Telegram into a 503 window by unsetting the webhook
     or pointing it at a static page. Document in the runbook.

3. **Snapshot source.**
   * `sqlite3 source.db ".backup snapshot.db"` (consistent hot copy).
   * Work off `snapshot.db` from here on; keep the original untouched
     until verified (rollback anchor).

4. **Copy, per table, in FK-safe order.**
   * Read in `BATCH_SIZE` (e.g. 1000) rows per table from SQLite with
     the existing SQLAlchemy models.
   * Transform at the row level:
     * UUID strings → `uuid.UUID(s)`.
     * ISO/epoch timestamps → `datetime` with UTC tz attached.
     * `0/1` ints on boolean columns → `bool`.
   * Bulk-insert into Postgres using `INSERT ... ON CONFLICT DO
     NOTHING` so the script is resumable on crash.
   * Tables are already small; if any ever grows (message mappings),
     switch to `COPY` via `psycopg.copy`.

5. **Reset sequences / identity columns** — not applicable (UUID v7,
   no autoincrement), but the script asserts no `serial`/`identity`
   columns were silently introduced.

6. **Verify.**
   * Row count match per table.
   * Spot-check: for each table, pick 10 random PKs from SQLite and
     assert every column (after type normalization) matches on Postgres.
   * Foreign-key integrity: `SELECT` self-join on Postgres to ensure no
     orphaned rows (shouldn't happen if order was correct; belt &
     braces).
   * Write a migration report (JSON) to `backups/migration-<ts>.json`:
     source row counts, target row counts, duration, any skipped rows.

7. **Flip config.**
   * `DATABASE_BACKEND=postgres`, `DATABASE_URL=postgres://…` in the
     ConfigMap/Secret.
   * `kubectl rollout restart deployment/worker-tasks
     deployment/worker-delivery webhook-receiver`.
   * Scale back to normal replica counts.
   * Queued updates from the maintenance window drain through.

8. **Post-migration soak (24h).**
   * Keep SQLite PVC + snapshot for 7 days.
   * Monitor: handler error rate, `delivery_queue` depth, Postgres
     connections, slow-query log.

Tests for the migration script itself (integration):
* Populate a SQLite DB with fixture data → run script → assert
  row-for-row equivalence on testcontainer Postgres.
* Crash mid-run → re-run → idempotent (thanks to `ON CONFLICT`).
* Refuses to run against non-empty target.
* Refuses to run if Alembic head on target doesn't match.

#### 4.4 Rollback plan

Until SQLite is deleted (keep for 7 days post-cutover):

* **Config rollback** (< 2 min): flip
  `DATABASE_BACKEND=sqlite` + point volume mount back, roll workers.
  Loses writes that happened on Postgres since cutover — acceptable
  only if we catch a problem within minutes.
* **Data rollback** (< 30 min): export Postgres deltas since cutover
  (all rows with `updated_at > cutover_ts`), hand-merge into SQLite
  snapshot with the reverse of the migration script (`scripts/
  migrate_postgres_to_sqlite.py`, symmetric). Use only if app is
  confirmed broken and downtime is acceptable.
* **Nuclear**: restore the Phase-0 SQLite snapshot, accept lost
  writes. Only if the above can't complete.

After the 7-day soak: delete `tg-bot-data` PVC, remove SQLite backend
from default factory path (keep the code for dev), update the
`backup-sqlite` CronJob to become `backup-postgres` (`pg_dump -Fc`,
gzip, same PVC + retention).

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
- [ ] Phase 2: `webhook-receiver` + `MODE=receiver` + `scaled` overlay + tests
- [ ] Phase 3: consistent-hash exchange + `handle_update` task +
      StatefulSet-per-shard + ordering tests
- [ ] Phase 4.1: `src/databases/postgres/` backend + factory registration + deps
- [ ] Phase 4.2: dual-dialect Alembic migration + CI job against both dialects
- [ ] Phase 4.3: `scripts/migrate_sqlite_to_postgres.py` + integration tests (fixture copy, idempotency, refusals)
- [ ] Phase 4.4: reverse script + documented rollback runbook
- [ ] Phase 4.5: `k8s/base/postgres.yaml` + `managed-postgres` overlay + `backup-postgres` CronJob; drop `tg-bot-data` PVC mount from workers
- [ ] Phase 5: HPAs + KEDA + Prometheus metrics + dashboard
- [ ] Phase 6: cutover, cleanup, README consolidation
