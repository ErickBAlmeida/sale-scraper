"""Dedup de ofertas repostadas por canais diferentes."""
import asyncio
import sqlite3

import pytest

from core.dedup import OfferFingerprint as FP
from core.dedup import extract_product_keys, is_duplicate
from db.database import Database
from listener.userbot import AccountListener
from tests.fakes import TZ, FakeBot, FakeTelethon, fake_config, fake_event


# ---------------- IDs de produto ----------------
@pytest.mark.parametrize(
    "text, key",
    [
        ("https://www.amazon.com.br/Placa-RTX-4070/dp/b0cxyz1234?tag=canal-20", "amazon:B0CXYZ1234"),
        ("amazon.com.br/gp/product/B0CXYZ1234/ref=x", "amazon:B0CXYZ1234"),
        ("https://produto.mercadolivre.com.br/MLB-3456789012-monitor-_JM", "mlb:3456789012"),
        ("https://www.mercadolivre.com.br/monitor/p/MLB19876543", "mlb:19876543"),
        ("https://shopee.com.br/Fone-Bluetooth-i.123456.7890123?af=1", "shopee:123456.7890123"),
        ("https://shopee.com.br/product/123456/7890123", "shopee:123456.7890123"),
        ("https://www.magazineluiza.com.br/air-fryer/p/abc123def4/ep/", "magalu:abc123def4"),
        ("https://www.magazinevoce.com.br/loja/air-fryer/p/ABC123DEF4/", "magalu:abc123def4"),
        ("https://www.kabum.com.br/produto/512345/ssd", "kabum:512345"),
        ("https://pt.aliexpress.com/item/1005006123456.html", "aliexpress:1005006123456"),
    ],
)
def test_extract_product_keys(text, key):
    assert extract_product_keys(f"🔥 Oferta! {text} corre") == {key}


def test_extract_product_keys_ignores_short_links():
    assert extract_product_keys("https://amzn.to/3xYz https://mercadolivre.com/sec/1ab") == frozenset()


# ---------------- regras de duplicata ----------------
AMZ = frozenset({"amazon:B0CXYZ1234"})


def test_same_term_same_price_is_duplicate():
    assert is_duplicate(FP("rtx 4070", 3499.0), [FP("rtx 4070", 3499.0)])


def test_price_drop_is_new_offer():
    assert not is_duplicate(FP("rtx 4070", 3299.0, AMZ), [FP("rtx 4070", 3499.0, AMZ)])


def test_same_product_without_price_is_duplicate():
    assert is_duplicate(FP("rtx 4070", None, AMZ), [FP("4070", 3499.0, AMZ)])


def test_same_term_without_price_is_not_duplicate():
    # Sem preço nem produto em comum não dá para afirmar que é a mesma oferta.
    assert not is_duplicate(FP("rtx 4070", None), [FP("rtx 4070", None)])


def test_different_term_same_price_is_not_duplicate():
    assert not is_duplicate(FP("rtx 4070", 3499.0), [FP("ryzen 7", 3499.0)])


# ---------------- listener ----------------
@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.sqlite"), TZ)
    d.init(owner_chat_id=999)
    return d


def _setup(db, **config):
    uid = db.get_or_create_user(1, "a").id
    for ch in ("canala", "canalb", "canalc"):
        db.add_source(uid, ch, None)
    db.add_keyword(uid, "rtx 4070", "RTX 4070", 10)
    bot = FakeBot()
    return AccountListener(FakeTelethon(), db, fake_config(**config), bot, uid, 1), bot


def test_repost_from_other_channel_is_filtered(db):
    listener, bot = _setup(db)

    async def run():
        await listener._on_new_message(fake_event("canala", "🔥 RTX 4070 por R$ 3.499 https://amzn.to/a"))
        await listener._on_new_message(fake_event("canalb", "RTX 4070 Dual\n💰 R$ 3.499,00\nvia @canalb"))
        await listener._on_new_message(fake_event("canalc", "Baixou! RTX 4070 R$ 3.299"))

    asyncio.run(run())
    assert len(bot.sent) == 2                       # original + queda de preço
    assert "canala" in bot.sent[0][1] and "canalc" in bot.sent[1][1]
    assert listener.alerts_deduped == 1


def test_concurrent_reposts_send_once(db):
    listener, bot = _setup(db)

    async def run():
        await asyncio.gather(
            listener._on_new_message(fake_event("canala", "RTX 4070 R$ 3.499")),
            listener._on_new_message(fake_event("canalb", "RTX 4070 por R$ 3499")),
        )

    asyncio.run(run())
    assert len(bot.sent) == 1


def test_window_zero_disables_cross_channel_dedup(db):
    listener, bot = _setup(db, dedup_window_hours=0)

    async def run():
        await listener._on_new_message(fake_event("canala", "RTX 4070 R$ 3.499"))
        await listener._on_new_message(fake_event("canalb", "RTX 4070 por R$ 3499"))

    asyncio.run(run())
    assert len(bot.sent) == 2


def test_old_alerts_outside_window_are_ignored(db):
    listener, bot = _setup(db)
    uid = listener.user_id
    db.record_sent(uid, "h-velho", "canala", None, FP("rtx 4070", 3499.0))
    with sqlite3.connect(db.engine.url.database) as conn:
        conn.execute("UPDATE sent_alerts SET sent_at = '2020-01-01T00:00:00-03:00'")

    asyncio.run(listener._on_new_message(fake_event("canalb", "RTX 4070 R$ 3.499")))
    assert len(bot.sent) == 1


def test_migration_adds_fingerprint_columns(tmp_path):
    path = str(tmp_path / "old.sqlite")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE sent_alerts (id INTEGER PRIMARY KEY, user_id INTEGER, offer_hash VARCHAR(64),"
            " source_channel VARCHAR(128), message_link VARCHAR(256), sent_at VARCHAR(40))"
        )
        conn.execute("INSERT INTO sent_alerts VALUES (1, 1, 'h', 'c', NULL, '2026-11-11T10:00:00-03:00')")
    d = Database(path, TZ)
    d.init(owner_chat_id=999)
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sent_alerts)")}
    assert {"term", "price", "product_keys"} <= cols
    assert d.already_sent(1, "h")
    assert d.recent_fingerprints(1, 24 * 365 * 10) == []  # alerta antigo sem impressão digital
