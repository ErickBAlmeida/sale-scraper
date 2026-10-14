"""Listener MTProto (Telethon): entra nos canais-fonte, captura cada nova
mensagem, roda o matching e envia os alertas via Bot API.
"""
import asyncio
import html
import logging

from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest

from core.matching import offer_hash
from core.text import normalize
from core.timeutils import in_quiet_hours, now

log = logging.getLogger("listener")

_SNIPPET_MAX = 400
_SEND_THROTTLE_S = 0.05  # ~20 msg/s global, com margem sobre o limite do Telegram


class Listener:
    def __init__(self, client: TelegramClient, db, config, bot):
        self.client = client
        self.db = db
        self.config = config
        self.bot = bot
        self._send_lock = asyncio.Lock()

    def register(self) -> None:
        self.client.add_event_handler(self._on_new_message, events.NewMessage())

    async def join_all_sources(self) -> None:
        for src in self.db.list_sources(active_only=True):
            try:
                await self.join(src.channel)
                log.info("Entrou/confirmou canal-fonte @%s", src.channel)
            except Exception as e:  # noqa: BLE001 - falha isolada não derruba o boot
                log.warning("Falha ao entrar em @%s: %s", src.channel, e)

    async def join(self, channel: str):
        """Faz o userbot entrar no canal. Retorna o título, se disponível."""
        entity = await self.client.get_entity(channel)
        await self.client(JoinChannelRequest(entity))
        return getattr(entity, "title", None)

    async def leave(self, channel: str) -> None:
        try:
            entity = await self.client.get_entity(channel)
            await self.client(LeaveChannelRequest(entity))
        except Exception as e:  # noqa: BLE001
            log.warning("Falha ao sair de @%s: %s", channel, e)

    async def _on_new_message(self, event) -> None:
        try:
            text = event.raw_text or ""
            if not text.strip():
                return
            chat = await event.get_chat()
            username = getattr(chat, "username", None)
            if not username:
                return
            src = self.db.get_active_source_by_username(username)
            if not src:
                return
            await self._dispatch(text, src.channel, username, event.message.id)
        except Exception:  # noqa: BLE001 - um update com erro não pode derrubar o processo
            log.exception("Erro ao processar mensagem recebida")

    async def _dispatch(self, text: str, channel: str, username: str, msg_id: int) -> None:
        norm = normalize(text)
        ohash = offer_hash(text, channel)
        link = f"https://t.me/{username}/{msg_id}"
        recipients = self.db.find_matches(norm)
        if not recipients:
            return
        current = now(self.config.timezone)
        for m in recipients:
            if in_quiet_hours(m.quiet_start, m.quiet_end, current):
                continue
            if self.db.already_sent(m.user_id, ohash):
                continue
            ok = await self._send_alert(m.chat_id, m.raw_term, channel, text, link)
            if ok:
                self.db.record_sent(m.user_id, ohash, channel, link)
            log.info(
                "match user=%s term=%s channel=@%s sent=%s", m.user_id, m.term, channel, ok
            )

    async def _send_alert(self, chat_id: int, term: str, channel: str, text: str, link: str) -> bool:
        snippet = text.strip()
        if len(snippet) > _SNIPPET_MAX:
            snippet = snippet[:_SNIPPET_MAX] + "…"
        body = (
            f'🔔 Alerta: "<b>{html.escape(term)}</b>"\n'
            f"🏷️ Fonte: @{html.escape(channel)}\n\n"
            f"💬 {html.escape(snippet)}\n\n"
            f'🔗 <a href="{link}">Ver oferta original</a>'
        )
        async with self._send_lock:
            for _ in range(3):
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=body,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                    await asyncio.sleep(_SEND_THROTTLE_S)
                    return True
                except RetryAfter as e:
                    wait = int(getattr(e, "retry_after", 5)) + 1
                    log.warning("FloodWait: aguardando %ss antes de reenviar", wait)
                    await asyncio.sleep(wait)
                except Forbidden:
                    log.info("Usuário %s bloqueou o bot; marcando inativo", chat_id)
                    self.db.mark_user_inactive(chat_id)
                    return False
                except TelegramError as e:
                    log.warning("Erro ao enviar para %s: %s", chat_id, e)
                    return False
            return False
