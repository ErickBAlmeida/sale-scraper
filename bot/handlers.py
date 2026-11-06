"""Handlers de comando do bot de notificação (python-telegram-bot)."""
import asyncio
import functools
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.access import (
    DISCLAIMER,
    ensure_access,
    erase_data,
    on_access_callback,
    on_erase_callback,
    pending,
    privacy,
    revoke_access,
)
from bot.login import build_login_handler, logout
from core.price import format_brl, split_max_price
from core.timeutils import format_duration, now
from core.validation import validate_keyword
from listener.userbot import JoinRateLimited

log = logging.getLogger("bot")

INTRO = (
    "👋 <b>Bem-vindo ao Sale Scraper!</b>\n\n"
    "Eu monitoro canais de ofertas e te aviso quando aparece um produto que você procura.\n\n"
    + DISCLAIMER
)

WELCOME = (
    INTRO
    + "\n\n<b>Como começar:</b>\n"
    "1. /login — conecte sua conta do Telegram (os canais são lidos com ela)\n"
    "2. <code>/source_add @canal</code> — escolha os canais de ofertas\n"
    "3. <code>/add iphone 18</code> — cadastre o que quer acompanhar\n\n"
    "Use /help para ver todos os comandos."
)

HELP = (
    "<b>Conta</b>\n"
    "/login — conecta sua conta do Telegram\n"
    "/logout — desconecta sua conta\n"
    "/privacidade — o que é guardado e como apagar\n"
    "/apagar_dados — apaga tudo sobre você\n\n"
    "<b>Canais</b>\n"
    "/source_add @canal — passa a monitorar um canal\n"
    "/source_remove @canal — para de monitorar\n"
    "/sources — seus canais monitorados\n\n"
    "<b>Alertas</b>\n"
    "/add &lt;palavra&gt; — cadastra um alerta (ex.: <code>/add air fryer</code>)\n"
    "   com preço máximo: <code>/add iphone 18 &lt;10000</code> (só avisa até R$ 10000)\n"
    "/list — lista seus alertas\n"
    "/remove &lt;nº|palavra&gt; — remove um alerta\n"
    "/clear — apaga todos os alertas\n"
    "/pause — pausa as notificações\n"
    "/resume — retoma as notificações\n"
    "/settings — horário silencioso e preferências\n"
    "/stats — quantos alertas você recebeu\n"
    "/help — esta ajuda"
)

NEED_LOGIN = (
    "🔐 Para usar o bot, conecte sua conta do Telegram com /login.\n"
    "Os canais de ofertas são lidos com a sua própria conta."
)


def _db(context):
    return context.application.bot_data["db"]


def _cfg(context):
    return context.application.bot_data["config"]


def _manager(context):
    return context.application.bot_data["manager"]


def _is_admin(update: Update, context) -> bool:
    return update.effective_user is not None and update.effective_user.id == _cfg(context).owner_id


def requires_account(func):
    """Bloqueia o comando para quem não foi aprovado ou não conectou a conta com /login."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = await ensure_access(update, context)
        if user is None:
            return None
        if not _db(context).has_active_account(user.id):
            await update.message.reply_text(NEED_LOGIN)
            return None
        return await func(update, context)

    return wrapper


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
    user = _db(context).get_or_create_user(u.id, u.username)
    if u.id == _cfg(context).owner_id or user.approval == "approved":
        await ensure_access(update, context)
        await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(INTRO, parse_mode=ParseMode.HTML)
    await ensure_access(update, context)  # responde "pedido enviado / em análise / recusado"


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


@requires_account
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usage = (
        "Uso: /add <palavra-chave> [<preço máximo]\n"
        "Ex.: /add rtx 4070\n"
        "Ex.: /add iphone 18 <10000"
    )
    full = " ".join(context.args).strip()
    if not full:
        await update.message.reply_text(usage)
        return
    raw, max_price = split_max_price(full)
    if not raw:
        await update.message.reply_text("❌ Informe o item antes do preço.\n" + usage)
        return
    if max_price is not None and max_price <= 0:
        await update.message.reply_text("❌ O preço máximo precisa ser maior que zero.")
        return
    term, err = validate_keyword(raw)
    if err:
        await update.message.reply_text(f"❌ A palavra-chave {err}")
        return
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    status = _db(context).add_keyword(
        user.id, term, raw, _cfg(context).max_keywords, max_price
    )
    limit = f" até {format_brl(max_price)}" if max_price is not None else ""
    if status == "limit":
        await update.message.reply_text(
            f"❌ Você atingiu o limite de {_cfg(context).max_keywords} alertas. "
            f"Use /remove para liberar espaço."
        )
    elif status == "dup":
        await update.message.reply_text(f'ℹ️ Você já tem um alerta para "{raw}"{limit}.')
    elif status == "updated":
        novo = f"preço máximo {format_brl(max_price)}" if max_price is not None else "sem limite de preço"
        await update.message.reply_text(f'🔄 Alerta "{raw}" atualizado: {novo}.')
    else:
        await update.message.reply_text(
            f'✅ Alerta "{raw}"{limit} criado. Vou te avisar quando aparecer em uma oferta.'
        )


@requires_account
async def list_kw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    kws = _db(context).list_keywords(user.id)
    if not kws:
        await update.message.reply_text("Você ainda não tem alertas. Use /add <palavra>.")
        return
    lines = [
        f"{i + 1}. {html.escape(k.raw_term)}"
        + (f" — até {format_brl(k.max_price)}" if k.max_price is not None else "")
        for i, k in enumerate(kws)
    ]
    await update.message.reply_text("🔖 <b>Seus alertas:</b>\n" + "\n".join(lines),
                                    parse_mode=ParseMode.HTML)


@requires_account
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


@requires_account
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


@requires_account
async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    _db(context).set_paused(user.id, True)
    await update.message.reply_text("⏸️ Notificações pausadas. Use /resume para voltar.")


@requires_account
async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    _db(context).set_paused(user.id, False)
    await update.message.reply_text("▶️ Notificações reativadas.")


@requires_account
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


@requires_account
async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    srcs = _db(context).list_sources(user.id, active_only=True)
    if not srcs:
        await update.message.reply_text(
            "Você ainda não monitora nenhum canal. Use /source_add @canal."
        )
        return
    lines = [
        f"• @{html.escape(s.channel)}" + (f" — {html.escape(s.title)}" if s.title else "")
        for s in srcs
    ]
    await update.message.reply_text(
        f"📡 <b>Seus canais monitorados ({len(srcs)}/{_cfg(context).max_sources}):</b>\n"
        + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


@requires_account
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


# ---------------- canais (por usuário, lidos com a conta dele) ----------------
@requires_account
async def source_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel = _clean_channel(" ".join(context.args))
    if not channel or channel.startswith("+") or channel == "joinchat":
        await update.message.reply_text(
            "Uso: /source_add @canal\n(o canal precisa ser público, com @nome)"
        )
        return
    u = update.effective_user
    db = _db(context)
    user = db.get_or_create_user(u.id, u.username)
    if db.get_user_source(user.id, channel):
        await update.message.reply_text(f"ℹ️ Você já monitora @{channel}.")
        return
    max_sources = _cfg(context).max_sources
    if db.count_active_sources(user.id) >= max_sources:
        await update.message.reply_text(
            f"❌ Você atingiu o limite de {max_sources} canais. Use /source_remove para liberar."
        )
        return
    listener = _manager(context).get(user.id)
    if listener is None or not listener.is_connected():
        await update.message.reply_text(
            "⚠️ Sua conta está desconectada no momento. Tente de novo em alguns minutos "
            "ou refaça o /login."
        )
        return
    try:
        title, joined_now = await listener.join(channel, rate_limited=True)
    except JoinRateLimited as e:
        minutes = max(1, round(e.wait_seconds / 60))
        await update.message.reply_text(
            f"⏳ Sua conta já entrou em {_cfg(context).joins_per_hour} canais na última hora. "
            f"Para não ser marcada como spam pelo Telegram, tente @{channel} de novo em "
            f"~{minutes} min."
        )
        return
    except Exception as e:  # noqa: BLE001
        log.info("user=%s não entrou em @%s: %s", user.id, channel, e)
        await update.message.reply_text(
            f"❌ Não consegui acessar @{channel}. Confira se o canal existe e é público."
        )
        return
    db.add_source(user.id, channel, title, joined_by_bot=joined_now)
    await update.message.reply_text(
        f"✅ Monitorando @{channel}" + (f" ({title})" if title else "")
    )


@requires_account
async def source_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel = _clean_channel(" ".join(context.args))
    if not channel:
        await update.message.reply_text("Uso: /source_remove @canal")
        return
    u = update.effective_user
    user = _db(context).get_or_create_user(u.id, u.username)
    removed = _db(context).remove_source(user.id, channel)
    if not removed:
        await update.message.reply_text(f"❌ @{channel} não estava na sua lista.")
        return
    listener = _manager(context).get(user.id)
    # Só sai do canal se foi o bot que entrou; se você já seguia, continua seguindo.
    if removed.joined_by_bot and listener is not None:
        await listener.leave(channel)
    await update.message.reply_text(f"✅ Parei de monitorar @{channel}.")


# ---------------- comandos de admin ----------------


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
            await _manager(context).handle_blocked(cid)
        except RetryAfter as e:
            await asyncio.sleep(int(getattr(e, "retry_after", 5)) + 1)
        except TelegramError:
            pass
    await update.message.reply_text(f"📢 Enviado para {sent} usuário(s).")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Saúde da aplicação (admin). Se o bot estiver fora do ar, não há resposta."""
    if not _is_admin(update, context):
        await update.message.reply_text("⛔ Comando restrito ao administrador.")
        return
    cfg = _cfg(context)
    db = _db(context)
    manager = _manager(context)
    current = now(cfg.timezone)

    def ago(dt) -> str:
        return f"há {format_duration((current - dt).total_seconds())}"

    st = db.global_stats()
    ms = manager.stats()
    admin = db.get_or_create_user(update.effective_user.id, update.effective_user.username)
    mine = manager.get(admin.id)

    lines = [
        "🟢 <b>Sale Scraper no ar</b>",
        f"⏱️ Ligado {ago(manager.started_at)} "
        f"(desde {manager.started_at.strftime('%d/%m %H:%M')})",
        f"🔌 Contas conectadas: {ms['connected']}/{st['accounts_active']}"
        + (" ⚠️" if ms["connected"] < st["accounts_active"] else " ✅")
        + (f" · {st['accounts_revoked']} revogada(s)" if st["accounts_revoked"] else ""),
        "",
        "<b>Desde que ligou (todas as contas)</b>",
        f"📨 Mensagens dos canais: {ms['msgs_received']}",
        f"🔔 Alertas enviados: {ms['alerts_sent']}"
        + (f" (⚠️ {ms['alerts_failed']} falharam)" if ms["alerts_failed"] else ""),
        "",
    ]
    if mine is None:
        lines.append("<b>Seus canais</b>\nSua conta não está conectada (/login).")
    else:
        srcs = db.list_sources(admin.id, active_only=True)
        lines.append(
            f"<b>Seus canais ({len(srcs)})</b> — conta "
            + ("conectada ✅" if mine.is_connected() else "DESCONECTADA ⚠️")
        )
        for s in srcs:
            n = mine.msgs_by_channel.get(s.channel, 0)
            last = mine.last_msg_by_channel.get(s.channel)
            when = f"última {ago(last)}" if last else "nenhuma ainda"
            lines.append(f"• @{html.escape(s.channel)}: {n} msg ({when})")
        if not srcs:
            lines.append("Nenhum canal monitorado.")

    last_alert = st["last_alert_at"]
    if last_alert:
        last_alert = ago(datetime.fromisoformat(last_alert))
    lines += [
        "",
        "<b>Geral</b>",
        f"👥 Usuários ativos: {st['users_active']}"
        + (f" ({st['users_paused']} pausados)" if st["users_paused"] else "")
        + (f" · {st['users_inactive']} bloquearam o bot" if st["users_inactive"] else ""),
        f"⏳ Pedidos de acesso pendentes: {st['users_pending']}"
        + (" (veja /pendentes)" if st["users_pending"] else ""),
        f"📡 Canais monitorados (todos os usuários): {st['sources_active']}",
        f"🔖 Palavras-chave: {st['keywords']}"
        + (f" ({st['keywords_with_price']} com preço máximo)" if st["keywords_with_price"] else ""),
        f"🔔 Alertas: {st['alerts_today']} hoje · {st['alerts_total']} no total",
        f"🕒 Último alerta: {last_alert or 'nenhum'}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------- erro global ----------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Erro não tratado em handler", exc_info=context.error)


def register_handlers(application: Application, db, config, manager, cipher) -> None:
    application.bot_data["db"] = db
    application.bot_data["config"] = config
    application.bot_data["manager"] = manager
    application.bot_data["cipher"] = cipher

    # Login primeiro: enquanto a conversa está ativa, ela recebe as mensagens de texto.
    application.add_handler(build_login_handler())
    application.add_handler(CommandHandler("logout", logout))

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
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("pendentes", pending))
    application.add_handler(CommandHandler("revogar_acesso", revoke_access))

    application.add_handler(CommandHandler("privacidade", privacy))
    application.add_handler(CommandHandler("apagar_dados", erase_data))

    application.add_handler(CallbackQueryHandler(on_access_callback, pattern=r"^access:"))
    application.add_handler(CallbackQueryHandler(on_erase_callback, pattern=r"^erase:"))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)
