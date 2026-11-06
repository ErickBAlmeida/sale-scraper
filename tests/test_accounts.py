"""Contas por usuário: criptografia, migração de `sources`, isolamento entre usuários,
listener por conta e o ClientManager (revogação de sessão)."""
import asyncio
import sqlite3

import pytest
from cryptography.fernet import Fernet

from core.crypto import SessionCipher, mask_phone
from db.database import Database
from listener.manager import REVOKED_MSG, ClientManager
from listener.userbot import AccountListener
from tests.fakes import TZ, FakeBot, FakeTelethon, fake_config, fake_event


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.sqlite"), TZ)
    d.init(owner_chat_id=999)
    return d


@pytest.fixture
def cipher():
    return SessionCipher(Fernet.generate_key().decode())


# ---------------- criptografia ----------------
def test_cipher_roundtrip(cipher):
    token = cipher.encrypt("1BVtsOK8Bu...sessao")
    assert "sessao" not in token
    assert cipher.decrypt(token) == "1BVtsOK8Bu...sessao"


def test_cipher_wrong_key_raises(cipher):
    other = SessionCipher(Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        other.decrypt(cipher.encrypt("x"))


def test_cipher_invalid_key():
    with pytest.raises(ValueError):
        SessionCipher("nao-e-uma-chave")


def test_mask_phone():
    assert mask_phone("5511999994321") == "+55•••••••4321"
    assert mask_phone("+55 (11) 99999-4321") == "+55•••••••4321"
    assert "99999" not in mask_phone("5511999994321")


# ---------------- migração de sources (formato antigo: global) ----------------
def test_migrates_global_sources_to_admin(tmp_path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, chat_id INTEGER UNIQUE, username VARCHAR(64),
            is_active INTEGER, is_paused INTEGER, quiet_start VARCHAR(5), quiet_end VARCHAR(5),
            created_at VARCHAR(40));
        INSERT INTO users VALUES (1, 999, 'admin', 1, 0, NULL, NULL, 'x');
        CREATE TABLE sources (id INTEGER PRIMARY KEY, channel VARCHAR(128) UNIQUE,
            title VARCHAR(256), status VARCHAR(16), added_at VARCHAR(40));
        INSERT INTO sources VALUES (1, 'fraguas84oficial', 'Fraguas', 'active', 'x');
        INSERT INTO sources VALUES (2, 'velho', NULL, 'removed', 'x');
        """
    )
    con.commit()
    con.close()

    d = Database(str(path), TZ)
    d.init(owner_chat_id=999)
    d.init(owner_chat_id=999)  # idempotente
    srcs = d.list_sources(1)
    assert [(s.channel, s.status, s.joined_by_bot) for s in srcs] == [
        ("fraguas84oficial", "active", 1),
        ("velho", "removed", 1),
    ]
    # Agora o mesmo canal pode ser de outro usuário também
    other = d.get_or_create_user(555, "b").id
    d.add_source(other, "fraguas84oficial", "Fraguas")
    assert d.get_user_source(other, "FRAGUAS84OFICIAL").channel == "fraguas84oficial"


# ---------------- contas ----------------
def test_account_crud(db):
    uid = db.get_or_create_user(1, "a").id
    assert not db.has_active_account(uid)
    db.save_account(uid, 1, "+55•••4321", "enc1")
    assert db.has_active_account(uid)
    db.save_account(uid, 1, "+55•••4321", "enc2")  # substitui, não duplica
    [acc] = db.list_active_accounts()
    assert (acc.session_enc, acc.chat_id) == ("enc2", 1)
    db.set_account_status(uid, "revoked")
    assert not db.has_active_account(uid)
    assert db.list_active_accounts() == []
    db.delete_account(uid)
    assert db.get_account(uid) is None


# ---------------- isolamento entre usuários ----------------
def test_sources_and_matches_are_per_user(db):
    a = db.get_or_create_user(1, "a").id
    b = db.get_or_create_user(2, "b").id
    db.add_source(a, "canala", None)
    db.add_source(b, "canalb", None)
    db.add_keyword(a, "monitor", "monitor", 10)
    assert db.get_user_source(a, "canala") and not db.get_user_source(a, "canalb")
    assert [s.channel for s in db.list_sources(b)] == ["canalb"]
    assert db.find_match_for_user(a, "monitor gamer", None)
    assert db.find_match_for_user(b, "monitor gamer", None) is None  # b não cadastrou "monitor"


def test_remove_source_reports_joined_by_bot(db):
    a = db.get_or_create_user(1, "a").id
    db.add_source(a, "jaseguia", None, joined_by_bot=False)
    db.add_source(a, "botentrou", None, joined_by_bot=True)
    assert db.remove_source(a, "jaseguia").joined_by_bot == 0
    assert db.remove_source(a, "botentrou").joined_by_bot == 1
    assert db.remove_source(a, "botentrou") is None  # já removido
    assert db.count_active_sources(a) == 0


def _listener(db, user_id, chat_id, bot):
    return AccountListener(FakeTelethon(), db, fake_config(), bot, user_id, chat_id)


def test_listener_only_alerts_its_owner(db):
    a = db.get_or_create_user(1, "a").id
    b = db.get_or_create_user(2, "b").id
    for uid, ch in ((a, "canala"), (b, "canalb")):
        db.add_source(uid, ch, None)
        db.add_keyword(uid, "monitor", "monitor", 10)
    bot = FakeBot()
    la = _listener(db, a, 1, bot)

    async def run():
        await la._on_new_message(fake_event("canalb", "Monitor 27 R$ 500"))  # canal do B
        await la._on_new_message(fake_event("canala", "Monitor 27 R$ 500", msg_id=7))
        await la._on_new_message(fake_event("canala", "Monitor 27 R$ 500", msg_id=8))  # repetido
        await la._on_new_message(fake_event("privado", "monitor", is_channel=False))

    asyncio.run(run())
    assert [c for c, _ in bot.sent] == [1]          # só o dono, uma vez (dedup)
    assert "t.me/canala/7" in bot.sent[0][1]
    assert la.msgs_received == 2 and la.msgs_by_channel == {"canala": 2}


def test_join_detects_already_member(db):
    from types import SimpleNamespace

    listener = _listener(db, 1, 1, FakeBot())
    listener.client.entities["seguido"] = SimpleNamespace(title="Seguido", left=False)

    async def run():
        return await listener.join("seguido"), await listener.join("novo")

    (t1, joined1), (t2, joined2) = asyncio.run(run())
    assert (t1, joined1) == ("Seguido", False)
    assert (t2, joined2) == ("Novo", True)
    assert len(listener.client.joined) == 1  # só entrou no canal novo


# ---------------- ClientManager ----------------
def _manager(db, cipher, clients):
    bot = FakeBot()
    it = iter(clients)
    m = ClientManager(db, fake_config(), bot, cipher, client_factory=lambda s="": next(it))
    return m, bot


def test_manager_starts_valid_and_revokes_invalid(db, cipher):
    a = db.get_or_create_user(1, "a").id
    b = db.get_or_create_user(2, "b").id
    db.save_account(a, 1, "+1", cipher.encrypt("sa"))
    db.save_account(b, 2, "+2", cipher.encrypt("sb"))
    good, bad = FakeTelethon(), FakeTelethon(revoked=True)
    m, bot = _manager(db, cipher, [good, bad])

    async def run():
        await m.start_all()
        await m.stop_all()

    asyncio.run(run())
    assert db.get_account(a).status == "active"
    assert db.get_account(b).status == "revoked"
    assert bot.sent == [(2, REVOKED_MSG)]


def test_manager_unreadable_session_is_revoked(db, cipher):
    a = db.get_or_create_user(1, "a").id
    db.save_account(a, 1, "+1", "lixo-nao-criptografado")
    m, bot = _manager(db, cipher, [])
    asyncio.run(m.start_all())
    assert db.get_account(a).status == "revoked"
    asyncio.run(m.stop_all())


def test_health_check_detects_revocation(db, cipher):
    a = db.get_or_create_user(1, "a").id
    db.save_account(a, 1, "+1", cipher.encrypt("sa"))
    client = FakeTelethon()
    m, bot = _manager(db, cipher, [client])

    async def run():
        await m.start_all()
        assert m.get(a) is not None
        client.revoked = True          # usuário encerrou a sessão em Dispositivos
        await m.check_all()
        await m.stop_all()

    asyncio.run(run())
    assert m.get(a) is None
    assert db.get_account(a).status == "revoked"
    assert bot.sent == [(1, REVOKED_MSG)]


def test_manager_remove_with_logout(db, cipher):
    a = db.get_or_create_user(1, "a").id
    client = FakeTelethon()
    m, _ = _manager(db, cipher, [])

    async def run():
        await client.connect()
        await m.add(a, 1, client)
        assert m.stats()["connected"] == 1
        await m.remove(a, log_out=True)

    asyncio.run(run())
    assert client.logged_out and m.get(a) is None
