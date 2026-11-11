"""Listener MTProto (Telethon) de UMA conta de usuário.

Cada usuário conecta a própria conta via /login; o ClientManager cria um
AccountListener por conta. O listener só considera os canais e as
palavras-chave do seu dono, e os alertas vão só para ele (via Bot API).
"""
import asyncio
import html
import logging
import time
from collections import deque

from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest

from core.dedup import OfferFingerprint, extract_product_keys, is_duplicate
from core.matching import offer_hash
from core.price import extract_price, format_brl
from core.text import normalize
from core.timeutils import in_quiet_hours, now

log = logging.getLogger("listener")

_SNIPPET_MAX = 400
_SEND_THROTTLE_S = 0.05  # ~20 msg/s global, com margem sobre o limite do Telegram

# Um único lock para todos os envios via Bot API (o limite de envio é do bot, não da conta).
_SEND_LOCK = asyncio.Lock()

_JOIN_WINDOW_S = 3600


class JoinRateLimited(Exception):
    """A conta já entrou em canais demais na última hora (proteção anti-spam)."""

    def __init__(self, wait_seconds: float):
        super().__init__(f"aguarde {int(wait_seconds)}s")
        self.wait_seconds = wait_seconds


class AccountListener:
    def __init__(self, client: TelegramClient, db, config, bot, user_id: int, chat_id: int):
        self.client = client
        self.db = db
        self.config = config
        self.bot = bot
        self.user_id = user_id    # id interno (users.id) do dono da conta
        self.chat_id = chat_id    # chat do dono no bot (destino dos alertas)
        # Contadores desde que o listener iniciou (zeram a cada restart). Usados no /status.
        self.started_at = now(config.timezone)
        self.msgs_received = 0
        self.msgs_by_channel: dict = {}      # canal -> quantidade
        self.last_msg_by_channel: dict = {}  # canal -> datetime da última mensagem
        self.alerts_sent = 0
        self.alerts_failed = 0
        self._join_times: deque = deque()  # momentos (monotonic) das últimas entradas em canais
        # Serializa checar-duplicata -> enviar -> registrar: sem isso, o mesmo post
        # chegando de dois canais quase juntos passaria nas duas checagens.
        self._dispatch_lock = asyncio.Lock()
        self.alerts_deduped = 0  # reposts de outros canais descartados
        # Chamado com o chat_id quando o dono bloqueia o bot (o ClientManager desconecta a conta).
        self.on_blocked = None

    def is_connected(self) -> bool:
        return self.client.is_connected()

    def register(self) -> None:
        self.client.add_event_handler(self._on_new_message, events.NewMessage())

    async def join_all_sources(self) -> None:
        for src in self.db.list_sources(self.user_id, active_only=True):
            try:
                await self.join(src.channel)
                log.info("user=%s entrou/confirmou canal @%s", self.user_id, src.channel)
            except Exception as e:  # noqa: BLE001 - falha isolada não derruba o boot
                log.warning("user=%s falha ao entrar em @%s: %s", self.user_id, src.channel, e)

    def join_wait_seconds(self) -> float:
        """Quanto falta para a conta poder entrar em outro canal (0 = pode agora)."""
        limit = getattr(self.config, "joins_per_hour", 0)
        if not limit:
            return 0
        current = time.monotonic()
        while self._join_times and current - self._join_times[0] >= _JOIN_WINDOW_S:
            self._join_times.popleft()
        if len(self._join_times) < limit:
            return 0
        return _JOIN_WINDOW_S - (current - self._join_times[0])

    async def join(self, channel: str, rate_limited: bool = False):
        """Faz a conta entrar no canal.

        Retorna (título, entrou_agora). `entrou_agora` é False se a conta já
        seguia o canal — nesse caso o /source_remove não deve sair dele.
        Com `rate_limited`, levanta JoinRateLimited se a conta já entrou em
        JOINS_PER_HOUR canais na última hora (entrar em muitos canais seguidos é
        um padrão que o anti-spam do Telegram pune). Canais que a conta já segue
        não contam.
        """
        entity = await self.client.get_entity(channel)
        already_member = getattr(entity, "left", True) is False
        if not already_member:
            if rate_limited:
                wait = self.join_wait_seconds()
                if wait > 0:
                    raise JoinRateLimited(wait)
            await self.client(JoinChannelRequest(entity))
            self._join_times.append(time.monotonic())
        return getattr(entity, "title", None), not already_member

    async def leave(self, channel: str) -> None:
        try:
            entity = await self.client.get_entity(channel)
            await self.client(LeaveChannelRequest(entity))
        except Exception as e:  # noqa: BLE001
            log.warning("user=%s falha ao sair de @%s: %s", self.user_id, channel, e)

    async def _on_new_message(self, event) -> None:
        try:
            if not event.is_channel and not event.is_group:
                return  # conversas privadas da conta não interessam (e não são lidas)
            chat = await event.get_chat()
            username = getattr(chat, "username", None)
            if not username:
                return
            src = self.db.get_user_source(self.user_id, username)
            if not src:
                return  # canal que o dono segue, mas não cadastrou no bot
            self.msgs_received += 1
            self.msgs_by_channel[src.channel] = self.msgs_by_channel.get(src.channel, 0) + 1
            self.last_msg_by_channel[src.channel] = now(self.config.timezone)
            text = event.raw_text or ""
            if not text.strip():
                return  # só mídia, sem texto para comparar
            await self._dispatch(text, src.channel, username, event.message.id)
        except Exception:  # noqa: BLE001 - um update com erro não pode derrubar o processo
            log.exception("user=%s erro ao processar mensagem recebida", self.user_id)

    async def _dispatch(self, text: str, channel: str, username: str, msg_id: int) -> None:
        norm = normalize(text)
        price = extract_price(text)
        m = self.db.find_match_for_user(self.user_id, norm, price)
        if not m:
            return
        if in_quiet_hours(m.quiet_start, m.quiet_end, now(self.config.timezone)):
            return
        ohash = offer_hash(text, channel)
        fingerprint = OfferFingerprint(m.term, price, extract_product_keys(text))
        window_h = getattr(self.config, "dedup_window_hours", 24)
        async with self._dispatch_lock:
            if self.db.already_sent(m.user_id, ohash):
                return
            if window_h > 0 and is_duplicate(
                fingerprint, self.db.recent_fingerprints(m.user_id, window_h)
            ):
                self.alerts_deduped += 1
                log.info(
                    "dup user=%s term=%s price=%s channel=@%s (repost já alertado)",
                    m.user_id, m.term, price, channel,
                )
                return
            link = f"https://t.me/{username}/{msg_id}"
            label = m.raw_term
            if m.max_price is not None:
                label += f" (até {format_brl(m.max_price)})"
            ok = await self._send_alert(m.chat_id, label, channel, text, link)
            if ok:
                self.alerts_sent += 1
                self.db.record_sent(m.user_id, ohash, channel, link, fingerprint)
            else:
                self.alerts_failed += 1
        log.info(
            "match user=%s term=%s price=%s max=%s channel=@%s sent=%s",
            m.user_id, m.term, price, m.max_price, channel, ok,
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
        async with _SEND_LOCK:
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
                    if self.on_blocked is not None:
                        # fora do lock: o handler desconecta a conta (inclusive este listener)
                        asyncio.create_task(self.on_blocked(chat_id))
                    return False
                except TelegramError as e:
                    log.warning("Erro ao enviar para %s: %s", chat_id, e)
                    return False
            return False
