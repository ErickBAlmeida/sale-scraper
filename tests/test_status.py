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


def _fake(db, user_id, connected=True, msgs=None):
    started = now(TZ) - timedelta(hours=2, minutes=5)
    listener = SimpleNamespace(
        started_at=started,
        msgs_received=sum((msgs or {}).values()),
        msgs_by_channel=dict(msgs or {}),
        last_msg_by_channel={c: now(TZ) - timedelta(minutes=3) for c in (msgs or {})},
        alerts_sent=4,
        alerts_failed=0,
        is_connected=lambda: connected,
    )
    app = SimpleNamespace(
        bot_data={
            "db": db,
            "listener": listener,
            "config": SimpleNamespace(owner_id=OWNER, timezone=TZ),
        }
    )
    msg = FakeMessage()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=user_id), message=msg)
    context = SimpleNamespace(application=app, args=[])
    return update, context, msg


def test_status_restricted_to_admin(db):
    update, context, msg = _fake(db, user_id=123)
    asyncio.run(status(update, context))
    assert "restrito ao administrador" in msg.replies[0][0]


def test_status_report(db):
    db.add_source("fraguas84oficial", "Fraguas")
    db.add_source("linksbrazil", "Links")
    update, context, msg = _fake(db, OWNER, msgs={"fraguas84oficial": 7})
    asyncio.run(status(update, context))
    text, kwargs = msg.replies[0]
    assert "Sale Scraper no ar" in text
    assert "Ligado há 2h 05min" in text
    assert "conectado ✅" in text
    assert "Mensagens dos canais: 7" in text
    assert "@fraguas84oficial: 7 msg (última há 3min)" in text
    assert "@linksbrazil: 0 msg (nenhuma ainda)" in text
    assert "Último alerta: nenhum" in text
    assert kwargs.get("parse_mode") == "HTML"


def test_status_shows_disconnected(db):
    update, context, msg = _fake(db, OWNER, connected=False)
    asyncio.run(status(update, context))
    assert "DESCONECTADO" in msg.replies[0][0]
