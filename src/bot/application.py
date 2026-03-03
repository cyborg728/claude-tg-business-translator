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
    AuthorizedUserRepository,
    BotSettingRepository,
    BusinessConnectionRepository,
    LanguageRepository,
    MessageMappingRepository,
    UserRepository,
)
from src.i18n import Translator
from src.services import TranslationService

from .handlers import BusinessHandlers, CommandHandlers, TranslateHandlers, TranslatorMenuHandlers
from .handlers.translate import _CB_CHANGE_LANG, _CB_LANG_PREFIX
from .handlers.translator_menu import (
    _CB_ACCESS,
    _CB_ACCESS_ADD,
    _CB_ACCESS_DEL_PREFIX,
    _CB_ACCESS_LIST,
    _CB_ADD,
    _CB_BACK,
    _CB_DEL_PREFIX,
    _CB_LIST,
    _CB_TOGGLE,
)

logger = logging.getLogger(__name__)

# Handler group for stateful text input (runs after group 0 business handlers).
_GROUP_TEXT_INPUT = 1


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
    authorized_user_repo = AuthorizedUserRepository(session_factory)
    language_repo = LanguageRepository(session_factory)

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
        authorized_user_repo=authorized_user_repo,
    )
    command_handlers = CommandHandlers(translator=translator)
    translator_menu = TranslatorMenuHandlers(
        settings=settings,
        translator=translator,
        allowed_user_repo=allowed_user_repo,
        bot_setting_repo=bot_setting_repo,
        authorized_user_repo=authorized_user_repo,
    )
    translate_handlers = TranslateHandlers(
        translator=translator,
        translation_service=translation_service,
        language_repo=language_repo,
        bot_setting_repo=bot_setting_repo,
        authorized_user_repo=authorized_user_repo,
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

    # Replies to bot notifications in any private chat (owner + authorized users).
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.REPLY
            & filters.TEXT
            & ~filters.COMMAND,
            business_handlers.handle_owner_reply,
        )
    )

    # /translator inline menu — callbacks.
    app.add_handler(CallbackQueryHandler(translator_menu.cb_add, pattern=f"^{_CB_ADD}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_list, pattern=f"^{_CB_LIST}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_delete, pattern=f"^{_CB_DEL_PREFIX}"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_toggle, pattern=f"^{_CB_TOGGLE}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_back, pattern=f"^{_CB_BACK}$"))
    app.add_handler(CallbackQueryHandler(translator_menu.cb_access, pattern=f"^{_CB_ACCESS}$"))
    app.add_handler(
        CallbackQueryHandler(translator_menu.cb_access_add, pattern=f"^{_CB_ACCESS_ADD}$")
    )
    app.add_handler(
        CallbackQueryHandler(translator_menu.cb_access_list, pattern=f"^{_CB_ACCESS_LIST}$")
    )
    app.add_handler(
        CallbackQueryHandler(
            translator_menu.cb_access_delete, pattern=f"^{_CB_ACCESS_DEL_PREFIX}"
        )
    )

    # /translate inline menu — callbacks.
    app.add_handler(
        CallbackQueryHandler(translate_handlers.cb_select_lang, pattern=f"^{_CB_LANG_PREFIX}")
    )
    app.add_handler(
        CallbackQueryHandler(translate_handlers.cb_change_lang, pattern=f"^{_CB_CHANGE_LANG}$")
    )

    # Commands available to all authorized users (owner + access list).
    _private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("translator", translator_menu.cmd_translator, filters=_private))
    app.add_handler(CommandHandler("translate", translate_handlers.cmd_translate, filters=_private))
    app.add_handler(CommandHandler("start", command_handlers.start))
    app.add_handler(CommandHandler("help", command_handlers.help_command))

    # Catch-all for unrecognised commands (owner only, to avoid spam).
    app.add_handler(
        MessageHandler(
            filters.Chat(settings.owner_chat_id) & filters.COMMAND,
            command_handlers.unknown,
        )
    )

    # Stateful plain-text input (group 1 — runs after business-message handlers).
    # Each handler is a no-op unless its own state flag is set in user_data.
    _plain_text = _private & ~filters.REPLY & filters.TEXT & ~filters.COMMAND
    app.add_handler(
        MessageHandler(_plain_text, translator_menu.handle_username_input),
        group=_GROUP_TEXT_INPUT,
    )
    app.add_handler(
        MessageHandler(_plain_text, translate_handlers.handle_text_input),
        group=_GROUP_TEXT_INPUT + 1,
    )

    logger.info("Application built — mode=%s locale=%s", settings.mode, settings.locale)
    return app
