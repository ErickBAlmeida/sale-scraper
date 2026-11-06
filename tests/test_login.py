"""Fluxo do /login (ConversationHandler) com Telegram e Telethon simulados,
/logout e o bloqueio de comandos para quem não conectou a conta."""
import asyncio
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from telegram.ext import ConversationHandler

from bot import login
from bot.handlers import list_kw, source_add, source_remove
from core.crypto import SessionCipher
from db.database import Database
from listener.manager import ClientManager
from tests.fakes import TZ, FakeBot, FakeTelethon, fake_config

USER = 4242  # id do usuário no Telegram (= chat id no privado)


class Msg:
    def __init__(self, replies, text=None, contact=None):
        self._replies = replies
        self.text = text
        self.contact = contact
        self.deleted = False

    async def reply_text(self, text, **kwargs):
        self._replies.append(text)

    async def delete(self):
        self.deleted = True


class Harness:
    """Uma conversa com o bot: guarda user_data entre as mensagens, como o PTB."""

    def __init__(self, tmp_path, client: FakeTelethon, approved=True, **cfg):
        self.db = Database(str(tmp_path / "t.sqlite"), TZ)
        self.db.init(owner_chat_id=999)
        if approved:
            uid = self.db.get_or_create_user(USER, "u").id
            self.db.set_approval(uid, "approved")
        self.cipher = SessionCipher(Fernet.generate_key().decode())
        self.client = client
        self.bot = FakeBot()
        self.manager = ClientManager(
            self.db, fake_config(**cfg), self.bot, self.cipher, client_factory=lambda s="": client
        )
        self.app = SimpleNamespace(
            bot_data={
                "db": self.db,
                "config": fake_config(**cfg),
                "manager": self.manager,
                "cipher": self.cipher,
            }
        )
        self.user_data = {}
        self.replies = []

    def _ctx(self, args=()):
        return SimpleNamespace(
            application=self.app, user_data=self.user_data, bot=self.bot, args=list(args)
        )

    def _update(self, msg, user_id=USER):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id, username="u"),
            effective_chat=SimpleNamespace(id=user_id),
            message=msg,
        )

    async def send(self, handler, text=None, contact=None, args=()):
        msg = Msg(self.replies, text=text, contact=contact)
        state = await handler(self._update(msg), self._ctx(args))
        return state, msg

    def last(self):
        return self.replies[-1]


def contact(user_id=USER, phone="5511999994321"):
    return SimpleNamespace(user_id=user_id, phone_number=phone)


def test_full_login_with_spaced_code(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER, code="12345"))

    async def run():
        assert (await h.send(login.login_start, "/login"))[0] == login.PHONE
        assert "Compartilhar meu número" in h.last()
        state, _ = await h.send(login.got_contact, contact=contact())
        assert state == login.CODE and h.client.phone == "+5511999994321"
        state, msg = await h.send(login.got_code, text="1 2 3 4 5")
        assert msg.deleted                      # mensagem com o código foi apagada
        assert state == ConversationHandler.END
        await h.manager.stop_all()

    asyncio.run(run())
    user = h.db.get_or_create_user(USER, "u")
    acc = h.db.get_account(user.id)
    assert acc.status == "active" and acc.phone_masked == "+55•••••••4321"
    assert h.cipher.decrypt(acc.session_enc) == "SESS"
    assert "conectada" in h.last()
    assert "login" not in h.user_data           # nada pendente


def test_login_with_two_factor(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER, password="segredo"))

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        state, _ = await h.send(login.got_code, text="1 2 3 4 5")
        assert state == login.PASSWORD
        state, msg = await h.send(login.got_password, text="errada")
        assert state == login.PASSWORD and msg.deleted and "Senha incorreta" in h.last()
        state, msg = await h.send(login.got_password, text="segredo")
        assert state == ConversationHandler.END and msg.deleted
        await h.manager.stop_all()

    asyncio.run(run())
    assert h.db.has_active_account(h.db.get_or_create_user(USER, "u").id)


def test_wrong_code_attempts_run_out(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER, code="12345"))

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        assert (await h.send(login.got_code, text="123"))[0] == login.CODE   # curto demais
        assert (await h.send(login.got_code, text="1 1 1 1 1"))[0] == login.CODE
        assert (await h.send(login.got_code, text="2 2 2 2 2"))[0] == login.CODE
        assert (await h.send(login.got_code, text="3 3 3 3 3"))[0] == ConversationHandler.END

    asyncio.run(run())
    assert "Tentativas esgotadas" in h.last()
    assert not h.client.is_connected()          # client temporário descartado


def test_rejects_someone_elses_contact(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER))

    async def run():
        await h.send(login.login_start, "/login")
        state, _ = await h.send(login.got_contact, contact=contact(user_id=777))
        assert state == login.PHONE
        await login._cleanup(h._ctx())

    asyncio.run(run())
    assert "não é o seu número" in h.last()
    assert not hasattr(h.client, "phone")       # nenhum código foi pedido


def test_rejects_when_logged_account_is_not_the_user(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=1111))  # conta logada ≠ quem pediu

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        await h.send(login.got_code, text="1 2 3 4 5")

    asyncio.run(run())
    assert "não é a sua" in h.last()
    assert h.client.logged_out
    assert h.db.get_account(h.db.get_or_create_user(USER, "u").id) is None


def test_cancel_disconnects_pending_client(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER))

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        assert h.client.is_connected()
        assert (await h.send(login.cancel, "/cancel"))[0] == ConversationHandler.END

    asyncio.run(run())
    assert not h.client.is_connected() and "login" not in h.user_data


def test_message_after_expiry_is_handled(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER))

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        await login._cleanup(h._ctx())          # simula o timer de 5 min disparando
        return await h.send(login.got_code, text="1 2 3 4 5")

    state, msg = asyncio.run(run())
    assert state == ConversationHandler.END and msg.deleted and "expirou" in h.last()


def test_login_respects_max_accounts(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER), max_accounts=1)
    other = h.db.get_or_create_user(1, "o").id
    h.db.save_account(other, 1, "+1", "x")
    state, _ = asyncio.run(h.send(login.login_start, "/login"))
    assert state == ConversationHandler.END and "limite de contas" in h.last()


# ---------------- depois do login ----------------
def _logged_in(tmp_path):
    h = Harness(tmp_path, FakeTelethon(me_id=USER))

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        await h.send(login.got_code, text="1 2 3 4 5")

    asyncio.run(run())
    return h


def test_commands_blocked_without_login(tmp_path):
    h = Harness(tmp_path, FakeTelethon())
    asyncio.run(h.send(list_kw, "/list"))
    assert "/login" in h.last() and "🔐" in h.last()


def test_source_add_and_remove_use_own_account(tmp_path):
    h = _logged_in(tmp_path)
    user_id = h.db.get_or_create_user(USER, "u").id
    h.client.entities["jaseguia"] = SimpleNamespace(title="Já Seguia", left=False)

    async def run():
        await h.send(source_add, "/source_add", args=["@Promos"])
        await h.send(source_add, "/source_add", args=["https://t.me/jaseguia"])
        await h.send(source_add, "/source_add", args=["t.me/+convite"])
        assert "público" in h.last()
        await h.send(source_remove, "/source_remove", args=["@promos"])
        await h.send(source_remove, "/source_remove", args=["@jaseguia"])

    asyncio.run(run())
    assert len(h.client.joined) == 1   # entrou só em @promos (já seguia @jaseguia)
    assert len(h.client.left) == 1     # e só saiu de @promos
    assert h.db.count_active_sources(user_id) == 0


def test_source_limit(tmp_path):
    h = _logged_in(tmp_path)
    h.app.bot_data["config"] = fake_config(max_sources=1)

    async def run():
        await h.send(source_add, "/source_add", args=["@um"])
        await h.send(source_add, "/source_add", args=["@dois"])

    asyncio.run(run())
    assert "limite de 1 canais" in h.last()


def test_logout_revokes_and_keeps_data(tmp_path):
    h = _logged_in(tmp_path)
    user_id = h.db.get_or_create_user(USER, "u").id
    h.db.add_keyword(user_id, "monitor", "monitor", 10)

    asyncio.run(h.send(login.logout, "/logout"))
    assert h.client.logged_out
    assert h.db.get_account(user_id) is None
    assert h.manager.get(user_id) is None
    assert [k.term for k in h.db.list_keywords(user_id)] == ["monitor"]
    assert "desconectada" in h.last()


@pytest.mark.parametrize("text, ok", [("1 2 3 4 5", True), ("12-345", True), ("1 2 3", False)])
def test_code_formats(tmp_path, text, ok):
    h = Harness(tmp_path, FakeTelethon(me_id=USER, code="12345"))

    async def run():
        await h.send(login.login_start, "/login")
        await h.send(login.got_contact, contact=contact())
        state, _ = await h.send(login.got_code, text=text)
        await h.manager.stop_all()
        await login._cleanup(h._ctx())
        return state

    state = asyncio.run(run())
    assert (state == ConversationHandler.END) is ok
