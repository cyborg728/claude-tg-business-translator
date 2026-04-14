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

### Phase 1 — Idempotency & rate-limit hardening (safety net)

Goal: fix correctness before changing topology.

* Add `update_id` dedup helper: `cache/idempotency.py` with `SETNX` + TTL.
* Wire it into the existing PTB webhook handler (rejects duplicate
  updates early).
* `src/tasks/delivery.py`:
  * Parse 429 response, honor `parameters.retry_after`.
  * Add DLQ (`delivery_dlq`) for terminal failures after N retries.
  * Expose Prometheus counters (sent, 429, 5xx, retried, dead-lettered).
* Tests: dedup hit/miss, TTL; 429→retry with correct countdown; DLQ.

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

### Phase 4 — Move state off the pod

Goal: drop the single-replica constraint tied to SQLite's RWO PVC.

* Add `src/databases/postgres/` sibling: models, repositories, session
  factory. Register in `src/databases/factory.py`.
* Alembic: single `alembic/env.py` stays, but drop SQLite-only tricks
  (none currently) and ensure migrations apply to both backends.
  UUID v7 stored as `uuid` type on Postgres, `text(36)` on SQLite.
* k8s:
  * `postgres.yaml` (StatefulSet, PVC, Service) in `base/`. Optional
    overlay to use a managed Postgres via Secret.
  * `migrate-job.yaml` already works — points to `database_url_sync`.
  * Remove `tg-bot-data` PVC mount from `worker-tasks` once Postgres is
    the configured backend.
* Tests: run the integration suite against Postgres via `testcontainers`
  (dev dep) in CI; keep SQLite suite for local speed.

Deployable: `worker-tasks` can run any replica count; SQLite remains for
local dev.

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
| Postgres migration data loss | Dual-write window optional; realistically v2 has no production data to preserve |
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

- [ ] Phase 1: dedup + 429 + DLQ + metrics + tests
- [ ] Phase 2: `webhook-receiver` + `MODE=receiver` + `scaled` overlay + tests
- [ ] Phase 3: consistent-hash exchange + `handle_update` task +
      StatefulSet-per-shard + ordering tests
- [ ] Phase 4: Postgres backend + testcontainers CI + manifests
- [ ] Phase 5: HPAs + KEDA + Prometheus metrics + dashboard
- [ ] Phase 6: cutover, cleanup, README consolidation
