"""Controle de acesso e privacidade.

- Aprovação pelo admin: quem chega fica "aguardando"; o admin recebe botões
  ✅ Aprovar / ❌ Recusar. Só aprovados usam o bot (inclusive o /login).
- /privacidade: o que é guardado, para quê, e como apagar.
- /apagar_dados: apaga tudo do usuário (sessão, canais, palavras, histórico).
- Admin: /pendentes e /revogar_acesso.
"""
import html
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

log = logging.getLogger("access")

DISCLAIMER = (
    "ℹ️ O Sale Scraper é um app <b>não oficial</b>, sem vínculo com o Telegram, "
    "que usa a API do Telegram. Veja /privacidade."
)

PRIVACY = (
    "🔒 <b>Privacidade — Sale Scraper</b>\n\n"
    "App <b>não oficial</b>, sem vínculo com o Telegram, que usa a API do Telegram.\n\n"
    "<b>O que é guardado</b>\n"
    "• Seu id e @username do Telegram\n"
    "• Suas palavras-chave, canais monitorados e o histórico de alertas enviados\n"
    "• Se você fizer /login: a sessão da sua conta (criptografada) e seu telefone "
    "<b>mascarado</b> (ex.: +55•••••4321). O número completo, o código e a senha "
    "não são guardados, e as mensagens com código e senha são apagadas na hora.\n\n"
    "<b>Para que</b>\n"
    "Só para ler os canais de ofertas que <b>você</b> cadastrou e te mandar alertas. "
    "Conversas privadas e grupos que você não cadastrou são ignorados. Nada é "
    "compartilhado ou vendido.\n\n"
    "<b>Quem tem acesso</b>\n"
    "O administrador que opera o bot (servidor e banco de dados).\n\n"
    "<b>Por quanto tempo</b>\n"
    "Até você apagar. Se você bloquear o bot, a sessão da sua conta é encerrada e apagada.\n\n"
    "<b>Seus controles</b>\n"
    "• /logout — desconecta sua conta (mantém palavras e canais)\n"
    "• /apagar_dados — apaga <b>tudo</b> sobre você\n"
    "• Telegram > Configurações > Dispositivos — encerra a sessão a qualquer momento"
)

PENDING_MSG = (
    "📨 Seu pedido de acesso foi enviado ao administrador.\n"
    "Você será avisado aqui assim que for aprovado."
)
STILL_PENDING_MSG = "⏳ Seu pedido de acesso ainda está em análise pelo administrador."
REJECTED_MSG = "⛔ Seu acesso a este bot não foi autorizado."
APPROVED_MSG = (
    "✅ Seu acesso ao Sale Scraper foi aprovado!\n\n"
    "Próximo passo: /login para conectar sua conta do Telegram.\n"
    "Antes, se quiser, leia /privacidade."
)


def _db(context):
    return context.application.bot_data["db"]


def _cfg(context):
    return context.application.bot_data["config"]


def _manager(context):
    return context.application.bot_data["manager"]


def _describe(u) -> str:
    """'Nome (@user, id 123)' para o admin identificar quem pediu acesso."""
    name = html.escape(getattr(u, "full_name", None) or getattr(u, "first_name", None) or "Sem nome")
    username = getattr(u, "username", None)
    handle = f"@{html.escape(username)}, " if username else ""
    return f"{name} ({handle}id <code>{u.id}</code>)"


def _approval_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Aprovar", callback_data=f"access:approve:{user_id}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"access:reject:{user_id}"),
        ]]
    )


async def ensure_access(update: Update, context: ContextTypes.DEFAULT_TYPE, reply: bool = True):
    """Retorna o UserDTO se o usuário está aprovado; senão responde e retorna None.

    Na primeira vez de um usuário novo, avisa o admin com os botões de aprovação.
    O admin (OWNER_ID) é sempre aprovado.
    """
    u = update.effective_user
    db = _db(context)
    user = db.get_or_create_user(u.id, u.username)
    if u.id == _cfg(context).owner_id:
        if user.approval != "approved":
            db.set_approval(user.id, "approved")
            user.approval = "approved"
        return user
    if user.approval == "approved":
        return user

    if user.approval == "new":
        db.set_approval(user.id, "pending")
        try:
            await context.bot.send_message(
                _cfg(context).owner_id,
                f"🆕 <b>Pedido de acesso</b>\n{_describe(u)}",
                parse_mode=ParseMode.HTML,
                reply_markup=_approval_keyboard(user.id),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Não consegui avisar o admin do pedido de %s: %s", user.id, e)
        msg = PENDING_MSG
    elif user.approval == "pending":
        msg = STILL_PENDING_MSG
    else:
        msg = REJECTED_MSG
    if reply and update.message is not None:
        await update.message.reply_text(msg)
    return None


# ---------------- callbacks de aprovação (admin) ----------------
async def on_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id != _cfg(context).owner_id:
        await q.answer("Só o administrador pode aprovar.", show_alert=True)
        return
    await q.answer()
    _, action, raw_id = (q.data or "").split(":", 2)
    db = _db(context)
    target = db.get_user(int(raw_id))
    if target is None:
        await q.edit_message_text("Usuário não encontrado (talvez tenha apagado os dados).")
        return
    who = f"{'@' + html.escape(target.username) if target.username else 'id'} <code>{target.chat_id}</code>"
    if action == "approve":
        db.set_approval(target.id, "approved")
        await q.edit_message_text(f"✅ Acesso aprovado: {who}", parse_mode=ParseMode.HTML)
        await _notify(context, target.chat_id, APPROVED_MSG)
    else:
        await _revoke(context, target, notify=False)
        await q.edit_message_text(f"❌ Acesso recusado: {who}", parse_mode=ParseMode.HTML)
        await _notify(context, target.chat_id, REJECTED_MSG)


async def _notify(context, chat_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id, text)
    except Exception as e:  # noqa: BLE001
        log.warning("Não consegui avisar %s: %s", chat_id, e)


async def _revoke(context, target, notify: bool = True) -> None:
    """Tira o acesso: encerra a sessão, apaga os dados e guarda só o 'recusado'."""
    if db_has_data(context, target.id):
        await _manager(context).erase_user(target.id, keep_user_row=True)
    _db(context).set_approval(target.id, "rejected")
    if notify:
        await _notify(context, target.chat_id, REJECTED_MSG)


def db_has_data(context, user_id: int) -> bool:
    db = _db(context)
    return (
        db.get_account(user_id) is not None
        or db.count_keywords(user_id) > 0
        or bool(db.list_sources(user_id))
    )


# ---------------- comandos de admin ----------------
async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != _cfg(context).owner_id:
        await update.message.reply_text("⛔ Comando restrito ao administrador.")
        return
    db = _db(context)
    users = db.list_users_by_approval("pending") + db.list_users_by_approval("new")
    if not users:
        await update.message.reply_text("Nenhum pedido de acesso pendente.")
        return
    for u in users:
        handle = f"@{html.escape(u.username)}, " if u.username else ""
        await update.message.reply_text(
            f"⏳ Pedido de acesso: {handle}id <code>{u.chat_id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_approval_keyboard(u.id),
        )


async def revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != _cfg(context).owner_id:
        await update.message.reply_text("⛔ Comando restrito ao administrador.")
        return
    ref = " ".join(context.args).strip()
    if not ref:
        await update.message.reply_text("Uso: /revogar_acesso <id ou @username>")
        return
    target = _db(context).find_user(ref)
    if ref.lstrip("@") == str(_cfg(context).owner_id) or (
        target is not None and target.chat_id == _cfg(context).owner_id
    ):
        await update.message.reply_text("❌ Você não pode revogar o próprio acesso.")
        return
    if target is None:
        await update.message.reply_text("❌ Usuário não encontrado.")
        return
    await _revoke(context, target)
    await update.message.reply_text(
        f"⛔ Acesso de id {target.chat_id} revogado: sessão encerrada e dados apagados."
    )


# ---------------- privacidade ----------------
async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(PRIVACY, parse_mode=ParseMode.HTML)


async def erase_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pede confirmação; funciona para qualquer usuário, aprovado ou não."""
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🗑️ Sim, apagar tudo", callback_data="erase:yes"),
            InlineKeyboardButton("Cancelar", callback_data="erase:no"),
        ]]
    )
    await update.message.reply_text(
        "⚠️ Isso vai <b>apagar tudo</b> sobre você: desconectar sua conta do Telegram, "
        "sair dos canais em que o bot entrou, e apagar suas palavras-chave, canais e "
        "histórico de alertas. Não dá para desfazer.\n\nConfirma?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def on_erase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if q.data != "erase:yes":
        await q.edit_message_text("Nada foi apagado.")
        return
    user: Optional[object] = _db(context).get_user_by_chat_id(q.from_user.id)
    if user is None:
        await q.edit_message_text("Não há dados seus guardados.")
        return
    is_admin = q.from_user.id == _cfg(context).owner_id
    # O admin mantém o registro (é sempre aprovado); os demais somem por completo.
    await _manager(context).erase_user(user.id, keep_user_row=is_admin)
    await q.edit_message_text(
        "🗑️ Pronto: seus dados foram apagados e sua sessão foi encerrada no Telegram.\n"
        + ("" if is_admin else "Se quiser voltar a usar, mande /start (será um novo pedido de acesso).")
    )
