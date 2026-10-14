"""Handlers de comando do bot de notificação (python-telegram-bot)."""
import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from core.validation import validate_keyword

log = logging.getLogger("bot")

WELCOME = (
    "👋 <b>Bem-vindo ao Alerta de Promoções!</b>\n\n"
    "Eu monitoro canais de ofertas e te aviso quando aparece um produto que você procura.\n\n"
    "Comece cadastrando uma palavra-chave:\n"
    "<code>/add rtx 4070</code>\n\n"
    "Use /help para ver todos os comandos."
)

HELP = (
    "<b>Comandos disponíveis</b>\n\n"
    "/add &lt;palavra&gt; — cadastra um alerta (ex.: <code>/add air fryer</code>)\n"
    "/list — lista seus alertas\n"
    "/remove &lt;nº|palavra&gt; — remove um alerta\n"
    "/clear — apaga todos os alertas\n"
    "/pause — pausa as notificações\n"
    "/resume — retoma as notificações\n"
    "/settings — horário silencioso e preferências\n"
    "/sources — canais monitorados\n"
    "/stats — quantos alertas você recebeu\n"
    "/help — esta ajuda"
)


def _db(context):
    return context.application.bot_data["db"]


def _cfg(context):
    return context.application.bot_data["config"]


def _is_admin(update: Update, context) -> bool:
    return update.effective_user is not None and update.effective_user.id == _cfg(context).owner_id


def _clean_channel(raw: str):
    s = raw.strip()
    if not s:
        return None
    s = s.replace("https://", "").replace("http://", "")
    if s.startswith("t.me/"):
        s = s[5:]
    if s.startswith("@"):
        s = s[1:]
    s = s.split("/")[0].split("?")[0]
    return s.lower() or None


# ---------------- comandos do usuário ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    _db(context).get_or_create_user(u.id, u.username)
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text("Uso: /add <palavra-chave>\nEx.: /add rtx 4070")
        return
    term, err = validate_keyword(raw)
    if err:
        await update.message.reply_text(f"❌ A palavra-chave {err}")
        return
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    status = _db(context).add_keyword(user.id, term, raw, _cfg(context).max_keywords)
    if status == "limit":
        await update.message.reply_text(
            f"❌ Você atingiu o limite de {_cfg(context).max_keywords} alertas. "
            f"Use /remove para liberar espaço."
        )
    elif status == "dup":
        await update.message.reply_text(f'ℹ️ Você já tem um alerta para "{raw}".')
    else:
        await update.message.reply_text(
            f'✅ Alerta "{raw}" criado. Vou te avisar quando aparecer em uma oferta.'
        )


async def list_kw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    kws = _db(context).list_keywords(user.id)
    if not kws:
        await update.message.reply_text("Você ainda não tem alertas. Use /add <palavra>.")
        return
    lines = [f"{i + 1}. {k.raw_term}" for i, k in enumerate(kws)]
    await update.message.reply_text("🔖 <b>Seus alertas:</b>\n" + "\n".join(lines),
                                    parse_mode=ParseMode.HTML)


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = " ".join(context.args).strip()
    if not arg:
        await update.message.reply_text("Uso: /remove <número ou palavra>")
        return
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    ok = _db(context).remove_keyword(user.id, arg)
    await update.message.reply_text(
        "✅ Alerta removido." if ok else "❌ Alerta não encontrado. Veja /list."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Sim, apagar tudo", callback_data="clear:yes"),
            InlineKeyboardButton("Cancelar", callback_data="clear:no"),
        ]]
    )
    await update.message.reply_text(
        "⚠️ Tem certeza que deseja apagar <b>todos</b> os seus alertas?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    _db(context).set_paused(user.id, True)
    await update.message.reply_text("⏸️ Notificações pausadas. Use /resume para voltar.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    _db(context).set_paused(user.id, False)
    await update.message.reply_text("▶️ Notificações reativadas.")


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    quiet = f"{user.quiet_start}–{user.quiet_end}" if user.quiet_start else "desligado"
    status = "pausado" if user.is_paused else "ativo"
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔕 Silêncio 00:00–08:00", callback_data="quiet:00:00:08:00")],
            [InlineKeyboardButton("🔕 Silêncio 23:00–07:00", callback_data="quiet:23:00:07:00")],
            [InlineKeyboardButton("🔔 Desligar silêncio", callback_data="quiet:off")],
        ]
    )
    await update.message.reply_text(
        f"⚙️ <b>Configurações</b>\n"
        f"Status: {status}\n"
        f"Horário silencioso: {quiet}",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    srcs = _db(context).list_sources(active_only=True)
    if not srcs:
        await update.message.reply_text("Nenhum canal monitorado ainda.")
        return
    lines = [f"• @{s.channel}" + (f" — {s.title}" if s.title else "") for s in srcs]
    await update.message.reply_text("📡 <b>Canais monitorados:</b>\n" + "\n".join(lines),
                                    parse_mode=ParseMode.HTML)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    today, total = _db(context).alert_stats(user.id)
    await update.message.reply_text(
        f"📊 <b>Alertas recebidos</b>\nHoje: {today}\nTotal: {total}", parse_mode=ParseMode.HTML
    )


# ---------------- callbacks (botões inline) ----------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    user = _db(context).get_or_create_user(q.from_user.id, q.from_user.username)
    if data == "clear:yes":
        n = _db(context).clear_keywords(user.id)
        await q.edit_message_text(f"🗑️ {n} alerta(s) apagado(s).")
    elif data == "clear:no":
        await q.edit_message_text("Operação cancelada.")
    elif data == "quiet:off":
        _db(context).set_quiet(user.id, None, None)
        await q.edit_message_text("🔔 Horário silencioso desligado.")
    elif data.startswith("quiet:"):
        parts = data.split(":")  # ['quiet','00','00','08','00']
        if len(parts) == 5:
            start = f"{parts[1]}:{parts[2]}"
            end = f"{parts[3]}:{parts[4]}"
            _db(context).set_quiet(user.id, start, end)
            await q.edit_message_text(f"🔕 Silêncio definido para {start}–{end}.")


# ---------------- comandos de admin ----------------
async def source_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update, context):
        await update.message.reply_text("⛔ Comando restrito ao administrador.")
        return
    channel = _clean_channel(" ".join(context.args))
    if not channel:
        await update.message.reply_text("Uso: /source_add @canal")
        return
    listener = context.application.bot_data["listener"]
    try:
        title = await listener.join(channel)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ Não consegui entrar em @{channel}: {e}")
        return
    _db(context).add_source(channel, title)
    await update.message.reply_text(
        f"✅ Monitorando @{channel}" + (f" ({title})" if title else "")
    )


async def source_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update, context):
        await update.message.reply_text("⛔ Comando restrito ao administrador.")
        return
    channel = _clean_channel(" ".join(context.args))
    if not channel:
        await update.message.reply_text("Uso: /source_remove @canal")
        return
    ok = _db(context).remove_source(channel)
    listener = context.application.bot_data["listener"]
    if ok:
        await listener.leave(channel)
    await update.message.reply_text(
        f"✅ Parei de monitorar @{channel}." if ok else f"❌ @{channel} não estava na lista."
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update, context):
        await update.message.reply_text("⛔ Comando restrito ao administrador.")
        return
    msg = " ".join(context.args).strip()
    if not msg:
        await update.message.reply_text("Uso: /broadcast <mensagem>")
        return
    ids = _db(context).all_active_chat_ids()
    sent = 0
    for cid in ids:
        try:
            await context.bot.send_message(cid, msg)
            sent += 1
            await asyncio.sleep(0.05)
        except Forbidden:
            _db(context).mark_user_inactive(cid)
        except RetryAfter as e:
            await asyncio.sleep(int(getattr(e, "retry_after", 5)) + 1)
        except TelegramError:
            pass
    await update.message.reply_text(f"📢 Enviado para {sent} usuário(s).")


# ---------------- erro global ----------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Erro não tratado em handler", exc_info=context.error)


def register_handlers(application: Application, db, config, listener) -> None:
    application.bot_data["db"] = db
    application.bot_data["config"] = config
    application.bot_data["listener"] = listener

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("list", list_kw))
    application.add_handler(CommandHandler("remove", remove))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("sources", sources))
    application.add_handler(CommandHandler("stats", stats))

    application.add_handler(CommandHandler("source_add", source_add))
    application.add_handler(CommandHandler("source_remove", source_remove))
    application.add_handler(CommandHandler("broadcast", broadcast))

    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)
