"""Testes do /status (admin): duração, totais do banco e a resposta montada."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from bot.handlers import status
from core.timeutils import format_duration, now
from db.database import Database

TZ = "America/Sao_Paulo"
OWNER = 999


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "0s"), (42, "42s"), (125, "2min"), (7500, "2h 05min"), (93784, "1d 2h 03min"), (-5, "0s")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.sqlite"), TZ)
    d.init()
    return d


def test_global_stats(db):
    a = db.get_or_create_user(1, "a").id
    b = db.get_or_create_user(2, "b").id
    db.get_or_create_user(3, "c")
    db.set_paused(b, True)
    db.mark_user_inactive(3)
    db.add_keyword(a, "monitor", "monitor", 10)
    db.add_keyword(a, "ryzen 5 5500", "Ryzen 5 5500", 10, 500.0)
    db.record_sent(a, "h1", "canal", "https://t.me/canal/1")

    st = db.global_stats()
    assert st["users_active"] == 2
    assert st["users_paused"] == 1
    assert st["users_inactive"] == 1
    assert st["keywords"] == 2
    assert st["keywords_with_price"] == 1
    assert st["alerts_today"] == 1 and st["alerts_total"] == 1
    assert st["last_alert_at"] is not None


# ---------------- handler com Telegram simulado ----------------
class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _fake(db, user_id, connected=True, msgs=None, has_listener=True):
    started = now(TZ) - timedelta(hours=2, minutes=5)
    admin = db.get_or_create_user(OWNER, "admin")
    listener = SimpleNamespace(
        msgs_received=sum((msgs or {}).values()),
        msgs_by_channel=dict(msgs or {}),
        last_msg_by_channel={c: now(TZ) - timedelta(minutes=3) for c in (msgs or {})},
        alerts_sent=4,
        alerts_failed=0,
        is_connected=lambda: connected,
    )
    listeners = {admin.id: listener} if has_listener else {}
    manager = SimpleNamespace(
        started_at=started,
        get=listeners.get,
        stats=lambda: {
            "connected": sum(1 for x in listeners.values() if x.is_connected()),
            "loaded": len(listeners),
            "msgs_received": sum(x.msgs_received for x in listeners.values()),
            "alerts_sent": sum(x.alerts_sent for x in listeners.values()),
            "alerts_failed": 0,
        },
    )
    app = SimpleNamespace(
        bot_data={
            "db": db,
            "manager": manager,
            "config": SimpleNamespace(owner_id=OWNER, timezone=TZ, max_sources=20),
        }
    )
    msg = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username="admin"), message=msg
    )
    context = SimpleNamespace(application=app, args=[])
    return update, context, msg


def test_status_restricted_to_admin(db):
    update, context, msg = _fake(db, user_id=123)
    asyncio.run(status(update, context))
    assert "restrito ao administrador" in msg.replies[0][0]


def _admin_with_account(db):
    admin = db.get_or_create_user(OWNER, "admin")
    db.save_account(admin.id, OWNER, "+55•••••4321", "enc")
    return admin


def test_status_report(db):
    admin = _admin_with_account(db)
    db.add_source(admin.id, "fraguas84oficial", "Fraguas")
    db.add_source(admin.id, "linksbrazil", "Links")
    other = db.get_or_create_user(555, "outro").id
    db.add_source(other, "canaldooutro", None)        # não aparece na seção "Seus canais"
    update, context, msg = _fake(db, OWNER, msgs={"fraguas84oficial": 7})
    asyncio.run(status(update, context))
    text, kwargs = msg.replies[0]
    assert "Sale Scraper no ar" in text
    assert "Ligado há 2h 05min" in text
    assert "Contas conectadas: 1/1 ✅" in text
    assert "Mensagens dos canais: 7" in text
    assert "Seus canais (2)" in text
    assert "@fraguas84oficial: 7 msg (última há 3min)" in text
    assert "@linksbrazil: 0 msg (nenhuma ainda)" in text
    assert "canaldooutro" not in text
    assert "Canais monitorados (todos os usuários): 3" in text
    assert "Último alerta: nenhum" in text
    assert kwargs.get("parse_mode") == "HTML"


def test_status_shows_disconnected(db):
    _admin_with_account(db)
    update, context, msg = _fake(db, OWNER, connected=False)
    asyncio.run(status(update, context))
    text = msg.replies[0][0]
    assert "Contas conectadas: 0/1 ⚠️" in text
    assert "DESCONECTADA" in text


def test_status_admin_without_account(db):
    update, context, msg = _fake(db, OWNER, has_listener=False)
    asyncio.run(status(update, context))
    assert "Sua conta não está conectada" in msg.replies[0][0]
