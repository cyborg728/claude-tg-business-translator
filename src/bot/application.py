import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    BusinessConnectionHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.config import Settings
from src.database.repositories import (
    BusinessConnectionRepository,
    MessageMappingRepository,
    UserRepository,
)
from src.i18n import Translator
from src.services import TranslationService

from .handlers import BusinessHandlers, CommandHandlers

logger = logging.getLogger(__name__)


def build_application(settings: Settings, session_factory) -> Application:
    """Assemble and return a fully-configured PTB Application.

    Parameters
    ----------
    settings:
        Application settings (token, owner ID, Gemini config, …).
    session_factory:
        An ``async_sessionmaker`` produced by ``Database.get_session_factory()``.
    """
    # ── Services & repositories ───────────────────────────────────────────────
    translator = Translator(settings.locale)
    translation_service = TranslationService(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
    )
    connection_repo = BusinessConnectionRepository(session_factory)
    message_repo = MessageMappingRepository(session_factory)
    user_repo = UserRepository(session_factory)

    # ── Handler classes ───────────────────────────────────────────────────────
    business_handlers = BusinessHandlers(
        settings=settings,
        translator=translator,
        translation_service=translation_service,
        connection_repo=connection_repo,
        message_repo=message_repo,
        user_repo=user_repo,
    )
    command_handlers = CommandHandlers(translator=translator)

    # ── Build PTB application ─────────────────────────────────────────────────
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .build()
    )

    # Business connection events (connect / disconnect).
    app.add_handler(BusinessConnectionHandler(business_handlers.handle_connection))

    # Incoming text messages from users via business connection.
    # We exclude the owner's own chat and commands.
    app.add_handler(
        MessageHandler(
            filters.UpdateType.BUSINESS_MESSAGE & filters.TEXT & ~filters.COMMAND,
            business_handlers.handle_incoming_message,
        )
    )

    # Owner replies to a bot notification in the private chat.
    app.add_handler(
        MessageHandler(
            filters.Chat(settings.owner_chat_id)
            & filters.REPLY
            & filters.TEXT
            & ~filters.COMMAND,
            business_handlers.handle_owner_reply,
        )
    )

    # Private-chat commands (visible only to the owner).
    app.add_handler(CommandHandler("start", command_handlers.start))
    app.add_handler(CommandHandler("help", command_handlers.help_command))

    # Catch-all for unrecognised commands.
    app.add_handler(
        MessageHandler(
            filters.Chat(settings.owner_chat_id) & filters.COMMAND,
            command_handlers.unknown,
        )
    )

    logger.info("Application built — mode=%s locale=%s", settings.mode, settings.locale)
    return app
