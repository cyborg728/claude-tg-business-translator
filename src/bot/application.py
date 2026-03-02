import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    BusinessConnectionHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.config import Settings
from src.database.repositories import (
    AllowedUserRepository,
    BotSettingRepository,
    BusinessConnectionRepository,
    MessageMappingRepository,
    UserRepository,
)
from src.i18n import Translator
from src.services import TranslationService

from .handlers import BusinessHandlers, CommandHandlers, TranslatorMenuHandlers
from .handlers.translator_menu import _CB_ADD, _CB_BACK, _CB_DEL_PREFIX, _CB_LIST, _CB_TOGGLE

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
    allowed_user_repo = AllowedUserRepository(session_factory)
    bot_setting_repo = BotSettingRepository(session_factory)

    # ── Handler classes ───────────────────────────────────────────────────────
    business_handlers = BusinessHandlers(
        settings=settings,
        translator=translator,
        translation_service=translation_service,
        connection_repo=connection_repo,
        message_repo=message_repo,
        user_repo=user_repo,
        allowed_user_repo=allowed_user_repo,
        bot_setting_repo=bot_setting_repo,
    )
    command_handlers = CommandHandlers(translator=translator)
    translator_menu = TranslatorMenuHandlers(
        translator=translator,
        allowed_user_repo=allowed_user_repo,
        bot_setting_repo=bot_setting_repo,
    )

    # ── Build PTB application ─────────────────────────────────────────────────
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .build()
    )

    # Business connection events (connect / disconnect).
    app.add_handler(BusinessConnectionHandler(business_handlers.handle_connection))

    # Incoming text messages from users via business connection.
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

    # Plain text from the owner (non-reply, non-command) — used for username input
    # in the /translator add flow.  The handler is a no-op unless the awaiting-state
    # is set, so it is safe to register unconditionally.
    app.add_handler(
        MessageHandler(
            filters.Chat(settings.owner_chat_id)
            & ~filters.REPLY
            & filters.TEXT
            & ~filters.COMMAND,
            translator_menu.handle_username_input,
        )
    )

    # /translator inline menu — callbacks.
    app.add_handler(CallbackQueryHandler(translator_menu.cb_add, pattern=f"^{_CB_ADD}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_list, pattern=f"^{_CB_LIST}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_delete, pattern=f"^{_CB_DEL_PREFIX}"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_toggle, pattern=f"^{_CB_TOGGLE}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_back, pattern=f"^{_CB_BACK}$"))

    # Private-chat commands (owner only).
    app.add_handler(
        CommandHandler("translator", translator_menu.cmd_translator,
                       filters=filters.Chat(settings.owner_chat_id))
    )
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
