"""Gerencia as contas conectadas: um TelegramClient + AccountListener por usuário.

- start_all(): no boot, conecta todas as contas ativas do banco.
- add():       registra uma conta recém-logada (/login), reaproveitando o client.
- remove():    desconecta (e, no /logout, revoga a sessão no Telegram).
- Health check periódico: sessão revogada pelo usuário (Configurações >
  Dispositivos) é detectada, a conta vira `revoked` e o usuário é avisado.
"""
import asyncio
import logging
from typing import Callable, Dict, Optional

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.updates import GetStateRequest

from core.timeutils import now
from listener.userbot import AccountListener

log = logging.getLogger("manager")

# Erros que significam "esta sessão não vale mais" (usuário revogou, conta banida etc.).
REVOKED_ERRORS = (
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)

HEALTH_INTERVAL_S = 300

REVOKED_MSG = (
    "⚠️ Sua conta do Telegram foi desconectada do Sale Scraper "
    "(sessão encerrada em Configurações > Dispositivos, ou expirada).\n"
    "Seus alertas estão pausados. Use /login para conectar de novo."
)


class ClientManager:
    def __init__(self, db, config, bot, cipher, client_factory: Optional[Callable] = None):
        self.db = db
        self.config = config
        self.bot = bot
        self.cipher = cipher
        self._factory = client_factory or self._default_factory
        self.listeners: Dict[int, AccountListener] = {}
        self.started_at = now(config.timezone)
        self._health_task: Optional[asyncio.Task] = None

    def _default_factory(self, session_str: str = "") -> TelegramClient:
        return TelegramClient(StringSession(session_str), self.config.api_id, self.config.api_hash)

    def new_client(self, session_str: str = "") -> TelegramClient:
        """Client novo (usado pelo /login com sessão vazia)."""
        return self._factory(session_str)

    # ---------------- ciclo de vida ----------------
    async def start_all(self) -> None:
        for acc in self.db.list_active_accounts():
            try:
                session_str = self.cipher.decrypt(acc.session_enc)
            except ValueError as e:
                log.error("user=%s sessão ilegível (%s); marcando revogada", acc.user_id, e)
                await self.revoke(acc.user_id, acc.chat_id)
                continue
            client = self._factory(session_str)
            try:
                await client.connect()
                await client(GetStateRequest())  # valida a sessão (levanta se revogada)
            except REVOKED_ERRORS:
                log.warning("user=%s sessão revogada ao iniciar", acc.user_id)
                await self._safe_disconnect(client)
                await self.revoke(acc.user_id, acc.chat_id)
                continue
            except Exception as e:  # noqa: BLE001 - rede etc.: tenta de novo no health check
                log.warning("user=%s falha ao conectar: %s", acc.user_id, e)
            await self._attach(acc.user_id, acc.chat_id, client)
        log.info("%s conta(s) conectada(s)", len(self.listeners))
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop())

    async def add(self, user_id: int, chat_id: int, client: TelegramClient) -> AccountListener:
        """Conta que acabou de fazer /login (client já conectado e autorizado)."""
        await self.remove(user_id)  # se havia uma conta antiga conectada, troca
        return await self._attach(user_id, chat_id, client)

    async def _attach(self, user_id: int, chat_id: int, client: TelegramClient) -> AccountListener:
        listener = AccountListener(client, self.db, self.config, self.bot, user_id, chat_id)
        listener.on_blocked = self.handle_blocked
        listener.register()
        self.listeners[user_id] = listener
        if client.is_connected():
            await listener.join_all_sources()
        return listener

    def get(self, user_id: int) -> Optional[AccountListener]:
        return self.listeners.get(user_id)

    async def remove(self, user_id: int, log_out: bool = False) -> None:
        listener = self.listeners.pop(user_id, None)
        if not listener:
            return
        if log_out:
            try:
                await listener.client.log_out()  # revoga a sessão no Telegram
                return
            except Exception as e:  # noqa: BLE001
                log.warning("user=%s log_out falhou: %s", user_id, e)
        await self._safe_disconnect(listener.client)

    async def revoke(self, user_id: int, chat_id: int, notify: bool = True) -> None:
        """Sessão perdeu a validade: marca no banco, para o listener e avisa o usuário."""
        self.db.set_account_status(user_id, "revoked")
        await self.remove(user_id)
        if notify:
            try:
                await self.bot.send_message(chat_id, REVOKED_MSG)
            except Exception as e:  # noqa: BLE001
                log.warning("user=%s não consegui avisar revogação: %s", user_id, e)

    async def handle_blocked(self, chat_id: int) -> None:
        """Usuário bloqueou o bot: encerra a sessão no Telegram e apaga a sessão salva.

        Não dá para avisar (o bot está bloqueado). Canais e palavras ficam salvos;
        se ele voltar, faz /login de novo.
        """
        user = self.db.get_user_by_chat_id(chat_id)
        if user is None or self.db.get_account(user.id) is None:
            return
        await self.remove(user.id, log_out=True)
        self.db.delete_account(user.id)
        log.info("user=%s bloqueou o bot; sessão encerrada e apagada", user.id)

    async def erase_user(self, user_id: int, keep_user_row: bool = False) -> dict:
        """Apaga tudo do usuário. Antes, sai dos canais em que o bot fez a conta entrar
        e encerra a sessão no Telegram (some de Configurações > Dispositivos)."""
        listener = self.listeners.get(user_id)
        if listener is not None and listener.is_connected():
            for src in self.db.list_sources(user_id, active_only=True):
                if src.joined_by_bot:
                    await listener.leave(src.channel)
        await self.remove(user_id, log_out=True)
        counts = self.db.delete_user_data(user_id, keep_user_row=keep_user_row)
        log.info("user=%s dados apagados %s", user_id, counts)
        return counts

    async def stop_all(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None
        for user_id in list(self.listeners):
            await self.remove(user_id)

    @staticmethod
    async def _safe_disconnect(client) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ---------------- saúde ----------------
    async def check_all(self) -> None:
        """Reconecta quem caiu e detecta sessões revogadas."""
        for user_id, listener in list(self.listeners.items()):
            client = listener.client
            try:
                if not client.is_connected():
                    await client.connect()
                    await listener.join_all_sources()
                await client(GetStateRequest())
            except REVOKED_ERRORS:
                log.warning("user=%s sessão revogada (health check)", user_id)
                await self.revoke(user_id, listener.chat_id)
            except Exception as e:  # noqa: BLE001
                log.warning("user=%s health check falhou: %s", user_id, e)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_INTERVAL_S)
            try:
                await self.check_all()
            except Exception:  # noqa: BLE001
                log.exception("Erro no health check das contas")

    # ---------------- /status ----------------
    def stats(self) -> dict:
        ls = list(self.listeners.values())
        return {
            "connected": sum(1 for x in ls if x.is_connected()),
            "loaded": len(ls),
            "msgs_received": sum(x.msgs_received for x in ls),
            "alerts_sent": sum(x.alerts_sent for x in ls),
            "alerts_failed": sum(x.alerts_failed for x in ls),
            "alerts_deduped": sum(x.alerts_deduped for x in ls),
        }
