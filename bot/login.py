"""/login e /logout: o usuário conecta a própria conta do Telegram ao bot.

Fluxo (ConversationHandler):
  /login → botão "📱 COMPARTILHAR MEU NÚMERO" (só aceita o número do próprio usuário)
        → Telethon envia o código ao app do usuário → usuário digita COM ESPAÇOS
          (o Telegram invalida códigos de login enviados "limpos" numa mensagem)
        → se houver verificação em 2 etapas, pede a senha
        → confere que a conta logada é a do próprio usuário → salva a sessão criptografada.

Mensagens com código e senha são apagadas assim que lidas. Nada disso vai para o log.
"""
import asyncio
import logging
import re

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from bot.access import ensure_access
from core.crypto import mask_phone

log = logging.getLogger("login")

PHONE, CODE, PASSWORD = range(3)
LOGIN_TIMEOUT_S = 300
MAX_ATTEMPTS = 3
CANCEL_LABEL = "Cancelar"

CONSENT = (
    "🔐 <b>Conectar sua conta do Telegram</b>\n\n"
    "O Sale Scraper é um app <b>não oficial</b>, sem vínculo com o Telegram, que usa a "
    "API do Telegram. Ele lê os canais de ofertas <b>com a sua própria conta</b>, e para "
    "isso precisa de uma sessão de login, igual a quando você entra no Telegram Desktop.\n\n"
    "• A sessão é guardada criptografada e usada só para ler os canais que você cadastrar.\n"
    "• Você pode desconectar quando quiser: /logout ou "
    "Telegram > Configurações > Dispositivos. Para apagar tudo: /apagar_dados.\n"
    "• Use por sua conta e risco: contas automatizadas ficam numa área cinzenta dos termos do Telegram.\n"
    "• Detalhes: /privacidade\n\n"
    "⚠️ Nunca envie seu código de login para outros bots ou pessoas. Aqui ele só é "
    "aceito logo após você tocar no botão abaixo, e a mensagem é apagada na hora.\n\n"
    "Para continuar, use o botão logo abaixo 👇"
)

CODE_PROMPT = (
    "📨 Enviei um código de login para o seu Telegram (chat oficial <b>Telegram</b>).\n\n"
    "Digite o código aqui <b>com espaços entre os números</b>, por exemplo: <code>1 2 3 4 5</code>\n"
    "⚠️ Sem os espaços o Telegram bloqueia o código.\n\n"
    "Para desistir: /cancel"
)


PHONE_LABEL = "📱 COMPARTILHAR MEU NÚMERO"

PHONE_PROMPT = (
    "👇 <b>Toque no botão grande abaixo</b> para compartilhar seu número.\n\n"
    "Não aparece? Toque no ícone de teclado (⌨️/🎛) ao lado do campo de mensagem.\n"
    "Para desistir: /cancel"
)


def _phone_keyboard() -> ReplyKeyboardMarkup:
    # O Telegram não permite mudar cor/estilo de botões; o destaque vem do tamanho:
    # um único botão, sem resize_keyboard, ocupa o teclado inteiro e fica fixo.
    return ReplyKeyboardMarkup(
        [[KeyboardButton(PHONE_LABEL, request_contact=True)]],
        resize_keyboard=False,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="👇 Toque no botão abaixo",
    )


def _db(context):
    return context.application.bot_data["db"]


def _cfg(context):
    return context.application.bot_data["config"]


def _manager(context):
    return context.application.bot_data["manager"]


async def _delete(update: Update) -> None:
    """Apaga a mensagem do usuário (código/senha). Bots podem apagar mensagens recebidas no privado."""
    try:
        await update.message.delete()
    except Exception as e:  # noqa: BLE001
        log.warning("Não consegui apagar mensagem sensível: %s", e)


async def _cleanup(context, disconnect: bool = True) -> None:
    """Descarta o login pendente (client temporário e timer)."""
    pending = context.user_data.pop("login", None)
    if not pending:
        return
    timer = pending.get("timer")
    if timer and timer is not asyncio.current_task():
        timer.cancel()
    if disconnect and pending.get("client") is not None:
        try:
            await pending["client"].disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _expire(context, chat_id: int) -> None:
    await asyncio.sleep(LOGIN_TIMEOUT_S)
    if "login" in context.user_data:
        await _cleanup(context)
        try:
            await context.bot.send_message(
                chat_id, "⌛ O login expirou. Use /login para tentar de novo.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception:  # noqa: BLE001
            pass


def _pending(context):
    return context.user_data.get("login")


async def _expired_reply(update: Update) -> int:
    await update.message.reply_text(
        "⌛ Esse login expirou. Use /login para começar de novo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ---------------- passos ----------------
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = await ensure_access(update, context)
    if user is None:
        return ConversationHandler.END  # não aprovado: ensure_access já respondeu
    db = _db(context)
    if db.has_active_account(user.id):
        acc = db.get_account(user.id)
        await update.message.reply_text(
            f"✅ Sua conta ({acc.phone_masked}) já está conectada.\n"
            "Para trocar de conta, use /logout e depois /login."
        )
        return ConversationHandler.END
    if db.count_active_accounts() >= _cfg(context).max_accounts:
        await update.message.reply_text(
            "😕 O bot atingiu o limite de contas conectadas. Tente novamente mais tarde."
        )
        return ConversationHandler.END

    await _cleanup(context)
    context.user_data["login"] = {
        "attempts": 0,
        "timer": asyncio.create_task(_expire(context, update.effective_chat.id)),
    }
    await update.message.reply_text(CONSENT, parse_mode="HTML")
    # Mensagem curta separada carregando o teclado: fica por último na tela, logo acima do botão.
    await update.message.reply_text(PHONE_PROMPT, parse_mode="HTML", reply_markup=_phone_keyboard())
    return PHONE


async def got_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pending = _pending(context)
    if pending is None:
        return await _expired_reply(update)
    contact = update.message.contact
    if contact.user_id != update.effective_user.id:
        await update.message.reply_text(
            f"❌ Esse não é o seu número. Use o botão <b>{PHONE_LABEL}</b> "
            "(só dá para conectar a sua própria conta).",
            parse_mode="HTML",
            reply_markup=_phone_keyboard(),
        )
        return PHONE

    phone = "+" + re.sub(r"\D", "", contact.phone_number)
    client = _manager(context).new_client()
    pending["client"] = client
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except FloodWaitError as e:
        await _cleanup(context)
        await update.message.reply_text(
            f"⏳ O Telegram pediu para esperar {max(1, e.seconds // 60)} min antes de "
            "um novo login. Tente /login depois.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END
    except (PhoneNumberInvalidError, PhoneNumberBannedError):
        await _cleanup(context)
        await update.message.reply_text(
            "❌ O Telegram recusou esse número para login.", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    except Exception as e:  # noqa: BLE001
        log.warning("Falha ao pedir código (%s): %s", mask_phone(phone), e)
        await _cleanup(context)
        await update.message.reply_text(
            "❌ Não consegui iniciar o login agora. Tente /login de novo em alguns minutos.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    pending["phone"] = phone
    pending["phone_code_hash"] = sent.phone_code_hash
    log.info("Código de login solicitado para %s", mask_phone(phone))
    await update.message.reply_text(CODE_PROMPT, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    return CODE


async def phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Não precisa digitar o número 🙂\n\n" + PHONE_PROMPT,
        parse_mode="HTML",
        reply_markup=_phone_keyboard(),
    )
    return PHONE


async def got_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pending = _pending(context)
    await _delete(update)
    if pending is None or "phone_code_hash" not in pending:
        return await _expired_reply(update)
    code = re.sub(r"\D", "", update.message.text or "")
    if not 5 <= len(code) <= 6:
        await update.message.reply_text(
            "O código tem 5 dígitos. Digite com espaços, ex.: <code>1 2 3 4 5</code>",
            parse_mode="HTML",
        )
        return CODE

    client = pending["client"]
    try:
        await client.sign_in(
            phone=pending["phone"], code=code, phone_code_hash=pending["phone_code_hash"]
        )
    except SessionPasswordNeededError:
        pending["attempts"] = 0
        await update.message.reply_text(
            "🔑 Sua conta tem <b>verificação em duas etapas</b>. Envie sua senha "
            "(a mensagem será apagada em seguida).\n\nPara desistir: /cancel",
            parse_mode="HTML",
        )
        return PASSWORD
    except PhoneCodeInvalidError:
        return await _retry(update, context, "❌ Código incorreto.", CODE)
    except PhoneCodeExpiredError:
        await _cleanup(context)
        await update.message.reply_text(
            "⌛ Esse código expirou ou foi bloqueado pelo Telegram (acontece quando o código é "
            "enviado sem espaços). Use /login para receber um novo."
        )
        return ConversationHandler.END
    except FloodWaitError as e:
        await _cleanup(context)
        await update.message.reply_text(
            f"⏳ Muitas tentativas. Aguarde {max(1, e.seconds // 60)} min e use /login de novo."
        )
        return ConversationHandler.END
    return await _finish(update, context)


async def got_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pending = _pending(context)
    await _delete(update)
    if pending is None or "client" not in pending:
        return await _expired_reply(update)
    try:
        await pending["client"].sign_in(password=update.message.text or "")
    except PasswordHashInvalidError:
        return await _retry(update, context, "❌ Senha incorreta.", PASSWORD)
    except FloodWaitError as e:
        await _cleanup(context)
        await update.message.reply_text(
            f"⏳ Muitas tentativas. Aguarde {max(1, e.seconds // 60)} min e use /login de novo."
        )
        return ConversationHandler.END
    return await _finish(update, context)


async def _retry(update: Update, context, msg: str, state: int) -> int:
    pending = _pending(context)
    pending["attempts"] += 1
    left = MAX_ATTEMPTS - pending["attempts"]
    if left <= 0:
        await _cleanup(context)
        await update.message.reply_text(f"{msg} Tentativas esgotadas. Use /login para recomeçar.")
        return ConversationHandler.END
    await update.message.reply_text(f"{msg} Tente de novo ({left} tentativa(s) restante(s)).")
    return state


async def _finish(update: Update, context) -> int:
    pending = _pending(context)
    client = pending["client"]
    me = await client.get_me()
    if me is None or me.id != update.effective_user.id:
        # Não deveria acontecer (o contato foi validado), mas nunca guardamos conta de terceiros.
        try:
            await client.log_out()
        except Exception:  # noqa: BLE001
            pass
        await _cleanup(context, disconnect=False)
        await update.message.reply_text("❌ A conta logada não é a sua. Login cancelado.")
        return ConversationHandler.END

    db = _db(context)
    user = db.get_or_create_user(update.effective_user.id, update.effective_user.username)
    cipher = context.application.bot_data["cipher"]
    masked = mask_phone(pending["phone"])
    db.save_account(user.id, me.id, masked, cipher.encrypt(client.session.save()))
    await _cleanup(context, disconnect=False)  # o client passa a ser do ClientManager
    await _manager(context).add(user.id, update.effective_chat.id, client)
    log.info("user=%s conectou a conta %s", user.id, masked)

    n_sources = db.count_active_sources(user.id)
    tip = (
        f"Seus {n_sources} canal(is) já estão sendo monitorados."
        if n_sources
        else "Agora cadastre um canal de ofertas: <code>/source_add @canal</code>\n"
        "e as palavras que quer acompanhar: <code>/add ryzen 5 5500 &lt;500</code>"
    )
    await update.message.reply_text(
        f"✅ <b>Conta {masked} conectada!</b>\n\n{tip}", parse_mode="HTML"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _cleanup(context)
    await update.message.reply_text("Login cancelado.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------- /logout ----------------
async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    db = _db(context)
    user = db.get_or_create_user(u.id, u.username)
    acc = db.get_account(user.id)
    if acc is None:
        await update.message.reply_text("Você não tem conta conectada. Use /login.")
        return
    await _manager(context).remove(user.id, log_out=True)
    db.delete_account(user.id)
    log.info("user=%s desconectou a conta %s", user.id, acc.phone_masked)
    await update.message.reply_text(
        f"👋 Conta {acc.phone_masked} desconectada e sessão encerrada no Telegram.\n"
        "Seus canais e palavras-chave continuam salvos — use /login para voltar."
    )


def build_login_handler() -> ConversationHandler:
    cancel_handlers = [
        CommandHandler("cancel", cancel),
        MessageHandler(filters.Regex(f"^{CANCEL_LABEL}$"), cancel),
    ]
    text = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            PHONE: [MessageHandler(filters.CONTACT, got_contact), *cancel_handlers[1:],
                    MessageHandler(text, phone_text)],
            CODE: [*cancel_handlers[1:], MessageHandler(text, got_code)],
            PASSWORD: [MessageHandler(text, got_password)],
        },
        fallbacks=[*cancel_handlers, CommandHandler("login", login_start)],
        allow_reentry=True,
        name="login",
    )
