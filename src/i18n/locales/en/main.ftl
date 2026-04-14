# Telegram Business Bot — English strings
# Syntax reference: https://projectfluent.org/

# ── /start ────────────────────────────────────────────────────────────────────
start-greeting = 👋 Hi, { $name }! I'm ready to work.
start-help =
    Available commands:
    /start        — this message
    /smoke        — end-to-end pipeline smoke test (enqueue → worker → reply)
    /redis_save   — save the next message text to Redis
    /redis_read   — read your saved text back from Redis

# ── /smoke ────────────────────────────────────────────────────────────────────
smoke-enqueued = ✅ Your request is queued. I'll reply as soon as the worker is done.
smoke-success = 🎉 success
smoke-failure = ❌ Something went wrong while processing your request.

# ── /redis_save & /redis_read ─────────────────────────────────────────────────
redis-save-prompt = 📝 Send the next message — I'll save it for you in Redis.
redis-save-done = 💾 Saved. Use /redis_read to retrieve it.
redis-save-cancelled = Save cancelled — I was already waiting for text.
redis-read-empty = 🗒 Nothing saved yet. Use /redis_save first.
redis-read-value = 📖 Your saved text:
    <code>{ $value }</code>

# ── Business messages ────────────────────────────────────────────────────────
business-connected = 🔌 Business connection added.
business-disconnected = 🔌 Business connection removed.
business-message-received = 📨 New message via your business account.

# ── Errors ────────────────────────────────────────────────────────────────────
error-unknown-command = 🤔 I don't know that command. Try /start.
error-internal = ⚠️ Oops — something went wrong. The incident has been logged.
