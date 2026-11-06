"""Aprovação de acesso pelo admin, /privacidade, /apagar_dados, /revogar_acesso,
limite de entradas em canais por hora e desconexão de quem bloqueia o bot."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from telegram.error import Forbidden

from bot import access
from bot.handlers import list_kw, source_add, start
from bot.login import login_start
from core.crypto import SessionCipher
from db.database import Database
from listener.manager import ClientManager
from listener.userbot import JoinRateLimited
from tests.fakes import TZ, FakeBot, FakeTelethon, fake_config, fake_event

OWNER = 999
USER = 4242


class Msg:
    def __init__(self, replies):
        self._replies = replies

    async def reply_text(self, text, **kwargs):
        self._replies.append(text)


class Query:
    def __init__(self, from_id, data):
        self.from_user = SimpleNamespace(id=from_id, username=None)
        self.data = data
        self.alerts = []
        self.edited = None

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edited = text


class World:
    def __init__(self, tmp_path):
        self.db = Database(str(tmp_path / "t.sqlite"), TZ)
        self.db.init(owner_chat_id=OWNER)
        self.cipher = SessionCipher(Fernet.generate_key().decode())
        self.bot = FakeBot()
        self.cfg = fake_config(owner_id=OWNER)
        self.manager = ClientManager(self.db, self.cfg, self.bot, self.cipher)
        self.app = SimpleNamespace(
            bot_data={"db": self.db, "config": self.cfg, "manager": self.manager, "cipher": self.cipher}
        )
        self.replies = []

    def ctx(self, args=()):
        return SimpleNamespace(application=self.app, bot=self.bot, user_data={}, args=list(args))

    def update(self, user_id=USER, username="fulano"):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id, username=username, full_name="Fulano"),
            effective_chat=SimpleNamespace(id=user_id),
            message=Msg(self.replies),
        )

    def run(self, handler, user_id=USER, args=()):
        return asyncio.run(handler(self.update(user_id), self.ctx(args)))

    def press(self, handler, from_id, data):
        q = Query(from_id, data)
        asyncio.run(handler(SimpleNamespace(callback_query=q), self.ctx()))
        return q

    def admin_msgs(self):
        return [t for c, t in self.bot.sent if c == OWNER]

    def user(self, chat_id=USER):
        return self.db.get_user_by_chat_id(chat_id)

    async def connect(self, chat_id=USER, client=None):
        """Usuário aprovado com conta conectada (como depois de um /login)."""
        uid = self.db.get_or_create_user(chat_id, "fulano").id
        self.db.set_approval(uid, "approved")
        self.db.save_account(uid, chat_id, "+55•••4321", self.cipher.encrypt("S"))
        client = client or FakeTelethon(me_id=chat_id)
        await client.connect()
        await self.manager.add(uid, chat_id, client)
        return uid, client


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


# ---------------- migração ----------------
def test_existing_users_become_approved(tmp_path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, chat_id INTEGER UNIQUE, username VARCHAR(64),
            is_active INTEGER, is_paused INTEGER, quiet_start VARCHAR(5), quiet_end VARCHAR(5),
            created_at VARCHAR(40));
        INSERT INTO users VALUES (1, 123, 'comconta', 1, 0, NULL, NULL, 'x');
        INSERT INTO users VALUES (2, 456, 'sostart', 1, 0, NULL, NULL, 'x');
        CREATE TABLE accounts (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, tg_user_id INTEGER,
            phone_masked VARCHAR(32), session_enc TEXT, status VARCHAR(16), created_at VARCHAR(40));
        INSERT INTO accounts VALUES (1, 1, 123, '+1', 'enc', 'active', 'x');
        """
    )
    con.commit()
    con.close()
    d = Database(str(path), TZ)
    d.init(owner_chat_id=OWNER)
    assert d.get_user_by_chat_id(123).approval == "approved"     # já tinha conta conectada
    assert d.get_user_by_chat_id(456).approval == "new"          # só deu /start: vai pedir acesso
    assert d.get_or_create_user(555, "novo").approval == "new"   # novos começam sem acesso


# ---------------- fluxo de aprovação ----------------
def test_new_user_requests_access_once(w):
    w.run(start)
    assert "não oficial" in w.replies[0]                  # aviso da API (termos 2.2)
    assert w.replies[1] == access.PENDING_MSG
    assert len(w.admin_msgs()) == 1 and "Pedido de acesso" in w.admin_msgs()[0]
    w.run(start)
    w.run(list_kw)
    assert w.replies[-1] == access.STILL_PENDING_MSG
    assert len(w.admin_msgs()) == 1                       # admin não é avisado de novo
    assert w.user().approval == "pending"


def test_pending_user_cannot_login(w):
    from telegram.ext import ConversationHandler

    w.run(start)
    assert w.run(login_start) == ConversationHandler.END
    assert w.replies[-1] == access.STILL_PENDING_MSG


def test_admin_approves(w):
    w.run(start)
    uid = w.user().id
    q = w.press(access.on_access_callback, OWNER, f"access:approve:{uid}")
    assert "aprovado" in q.edited
    assert w.user().approval == "approved"
    assert (USER, access.APPROVED_MSG) in w.bot.sent
    w.run(list_kw)
    assert "/login" in w.replies[-1]                      # agora só falta conectar a conta


def test_only_admin_can_press_approve(w):
    w.run(start)
    uid = w.user().id
    q = w.press(access.on_access_callback, 777, f"access:approve:{uid}")
    assert q.alerts and w.user().approval == "pending"


def test_admin_rejects(w):
    w.run(start)
    w.press(access.on_access_callback, OWNER, f"access:reject:{w.user().id}")
    assert w.user().approval == "rejected"
    assert (USER, access.REJECTED_MSG) in w.bot.sent
    w.run(start)
    assert w.replies[-1] == access.REJECTED_MSG
    assert len(w.admin_msgs()) == 1


def test_owner_is_always_approved(w):
    w.run(start, user_id=OWNER)
    assert "Como começar" in w.replies[-1]
    assert w.user(OWNER).approval == "approved"
    assert w.admin_msgs() == []


def test_pending_list(w):
    w.run(start)
    w.replies.clear()
    w.run(access.pending, user_id=OWNER)
    assert "Pedido de acesso" in w.replies[0]
    w.run(access.pending, user_id=USER)
    assert "restrito" in w.replies[-1]


# ---------------- privacidade e exclusão ----------------
def test_privacy_text(w):
    w.run(access.privacy)
    text = w.replies[-1]
    for must in ("não oficial", "mascarado", "/apagar_dados", "/logout", "Dispositivos"):
        assert must in text


def test_erase_everything(w):
    async def scenario():
        uid, client = await w.connect()
        client.entities["jaseguia"] = SimpleNamespace(title="J", left=False)
        listener = w.manager.get(uid)
        for ch in ("botentrou", "jaseguia"):
            _, joined = await listener.join(ch)
            w.db.add_source(uid, ch, None, joined_by_bot=joined)
        w.db.add_keyword(uid, "monitor", "monitor", 10)
        w.db.record_sent(uid, "h", "botentrou", None)
        return uid, client

    uid, client = asyncio.run(scenario())
    q = w.press(access.on_erase_callback, USER, "erase:yes")
    assert "apagados" in q.edited
    assert client.logged_out                               # sessão encerrada no Telegram
    assert len(client.left) == 1                           # só saiu do canal que o bot entrou
    assert w.user() is None                                # nada sobrou
    assert w.db.get_account(uid) is None and w.db.list_keywords(uid) == []
    assert w.db.list_sources(uid) == [] and w.db.alert_stats(uid) == (0, 0)


def test_erase_cancel(w):
    w.run(start)
    q = w.press(access.on_erase_callback, USER, "erase:no")
    assert "Nada foi apagado" in q.edited and w.user() is not None


def test_revoke_access(w):
    async def scenario():
        uid, client = await w.connect()
        w.db.add_keyword(uid, "monitor", "monitor", 10)
        return uid, client

    uid, client = asyncio.run(scenario())
    w.run(access.revoke_access, user_id=OWNER, args=["@fulano"])
    assert client.logged_out and w.manager.get(uid) is None
    assert w.user().approval == "rejected"                 # registro mínimo fica, como recusado
    assert w.db.list_keywords(uid) == [] and w.db.get_account(uid) is None
    assert (USER, access.REJECTED_MSG) in w.bot.sent
    w.run(access.revoke_access, user_id=OWNER, args=[str(OWNER)])
    assert "próprio acesso" in w.replies[-1]


# ---------------- anti-spam: entradas em canais por hora ----------------
def test_join_rate_limit(w):
    async def scenario():
        uid, client = await w.connect()
        listener = w.manager.get(uid)
        client.entities["jaseguia"] = SimpleNamespace(title="J", left=False)
        for i in range(5):
            await listener.join(f"canal{i}", rate_limited=True)
        await listener.join("jaseguia", rate_limited=True)    # já seguia: não conta
        with pytest.raises(JoinRateLimited) as exc:
            await listener.join("canal5", rate_limited=True)
        await listener.join("canal6")                         # boot/reconexão não é limitado
        return exc.value.wait_seconds

    wait = asyncio.run(scenario())
    assert 3500 < wait <= 3600


def test_source_add_reports_rate_limit(w):
    async def scenario():
        uid, _ = await w.connect()
        listener = w.manager.get(uid)
        for i in range(5):
            await listener.join(f"c{i}")
        await source_add(w.update(), w.ctx(["@mais_um"]))
        return uid

    uid = asyncio.run(scenario())
    assert "última hora" in w.replies[-1]
    assert w.db.count_active_sources(uid) == 0


# ---------------- quem bloqueia o bot ----------------
class BlockedBot(FakeBot):
    async def send_message(self, chat_id=None, text=None, **kwargs):
        raise Forbidden("bot was blocked by the user")


def test_blocking_the_bot_ends_session(w):
    w.manager.bot = BlockedBot()

    async def scenario():
        uid, client = await w.connect()
        w.manager.get(uid).bot = w.manager.bot
        w.db.add_source(uid, "canala", None)
        w.db.add_keyword(uid, "monitor", "monitor", 10)
        await w.manager.get(uid)._on_new_message(fake_event("canala", "Monitor 27"))
        await asyncio.sleep(0)                               # deixa a tarefa de desconexão rodar
        await asyncio.sleep(0)
        return uid, client

    uid, client = asyncio.run(scenario())
    assert client.logged_out
    assert w.db.get_account(uid) is None
    assert w.manager.get(uid) is None
    assert [k.term for k in w.db.list_keywords(uid)] == ["monitor"]  # o resto fica


def test_broadcast_only_reaches_approved(w):
    from bot.handlers import broadcast

    w.run(start)                                              # pendente
    approved = w.db.get_or_create_user(321, "ok").id
    w.db.set_approval(approved, "approved")
    w.bot.sent.clear()
    w.run(broadcast, user_id=OWNER, args=["oi"])
    assert [c for c, _ in w.bot.sent] == [321]
