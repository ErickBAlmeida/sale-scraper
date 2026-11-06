"""Testes do filtro de preço: leitura do cadastro, do post, e matching no banco."""
import sqlite3

import pytest

from core.price import extract_price, format_brl, split_max_price, within_max
from core.text import normalize
from db.database import Database

TZ = "America/Sao_Paulo"


# ---------------- cadastro: "item <preço" ----------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ryzen 5 5500 <500", ("ryzen 5 5500", 500.0)),
        ("ryzen 5 5500 < 500", ("ryzen 5 5500", 500.0)),
        ("ryzen 5 5500 <=500", ("ryzen 5 5500", 500.0)),
        ("monitor 27 <R$ 1.500", ("monitor 27", 1500.0)),
        ("monitor <r$1.299,90", ("monitor", 1299.90)),
        ("headset <99,9", ("headset", 99.90)),
        ("ryzen 5 5500", ("ryzen 5 5500", None)),
        ("rtx 4070 500", ("rtx 4070 500", None)),   # sem "<" não é preço
        ("<500", ("", 500.0)),
    ],
)
def test_split_max_price(raw, expected):
    assert split_max_price(raw) == expected


# ---------------- post: preço da oferta ----------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Placa-mãe MSI B550M\n\n💳 R$ 546 no PIX com cupom:", 546.0),
        ("Placa Mãe Asus A520m\n\n💴 R$ 422 em 9x sem juros", 422.0),
        ("Placa de Vídeo RTX 5080\n\n🪙 R$ 10940\n\nLink", 10940.0),
        ("Notebook R$1.299,90 à vista", 1299.90),
        ("De R$ 999 por R$ 546", 546.0),
        ("R$ 1.299 ou 12x de R$ 108,25", 1299.0),   # parcela ignorada
        ("R$ 999 em 10x R$ 99,90", 999.0),           # parcela sem "de"
        ("Monitor 27 polegadas 144hz", None),        # sem preço
        ("", None),
    ],
)
def test_extract_price(text, expected):
    assert extract_price(text) == expected


def test_within_max():
    assert within_max(546, None)        # sem limite: sempre passa
    assert within_max(None, None)
    assert within_max(500, 500)         # no limite: passa
    assert not within_max(546, 500)
    assert not within_max(None, 500)    # com limite e post sem preço: não passa


def test_format_brl():
    assert format_brl(500) == "R$ 500"
    assert format_brl(1500) == "R$ 1.500"
    assert format_brl(1299.9) == "R$ 1.299,90"


# ---------------- banco: cadastro e matching ----------------
@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.sqlite"), TZ)
    d.init()
    return d


def _user(db):
    return db.get_or_create_user(111, "tester").id


def test_add_keyword_with_price_and_update(db):
    uid = _user(db)
    assert db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, 500.0) == "ok"
    assert db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, 500.0) == "dup"
    assert db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, 450.0) == "updated"
    assert db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, None) == "updated"
    [kw] = db.list_keywords(uid)
    assert kw.max_price is None


def test_find_matches_respects_max_price(db):
    uid = _user(db)
    db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, 500.0)
    text = normalize("Processador AMD Ryzen 5 5500")
    assert db.find_matches(text, 499.0)
    assert db.find_matches(text, 500.0)
    assert not db.find_matches(text, 546.0)
    assert not db.find_matches(text, None)       # post sem preço


def test_find_matches_without_max_price_ignores_price(db):
    uid = _user(db)
    db.add_keyword(uid, "monitor", "monitor", 10, None)
    text = normalize("Monitor Gamer AOC")
    assert db.find_matches(text, 9999.0)
    assert db.find_matches(text, None)


def test_find_matches_falls_back_to_other_keyword(db):
    """Se a primeira palavra casa mas estoura o preço, outra palavra ainda pode alertar."""
    uid = _user(db)
    db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, 500.0)
    db.add_keyword(uid, "b550m", "B550M", 10, None)
    [m] = db.find_matches(normalize("Kit Ryzen 5 5500 + B550M"), 900.0)
    assert m.term == "b550m"


def test_remove_accepts_price_suffix(db):
    uid = _user(db)
    db.add_keyword(uid, "ryzen 5 5500", "Ryzen 5 5500", 10, 500.0)
    assert db.remove_keyword(uid, "Ryzen 5 5500 <500")
    assert db.list_keywords(uid) == []


def test_migration_adds_column_and_converts_old_keywords(tmp_path):
    """Banco antigo (sem max_price) com "Ryzen 5 5500 <500" salvo como texto."""
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY, user_id INTEGER, term VARCHAR(80),
            raw_term VARCHAR(80), created_at VARCHAR(40),
            CONSTRAINT uq_user_term UNIQUE (user_id, term)
        );
        INSERT INTO keywords VALUES (1, 1, 'ryzen 5 5500 <500', 'Ryzen 5 5500 <500', 'x');
        INSERT INTO keywords VALUES (2, 1, '520', '520', 'x');
        """
    )
    con.commit()
    con.close()

    d = Database(str(path), TZ)
    d.init()
    d.init()  # idempotente
    kws = {k.id: k for k in d.list_keywords(1)}
    assert (kws[1].term, kws[1].raw_term, kws[1].max_price) == ("ryzen 5 5500", "Ryzen 5 5500", 500.0)
    assert (kws[2].term, kws[2].max_price) == ("520", None)
