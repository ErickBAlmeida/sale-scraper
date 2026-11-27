"""Testes do núcleo: normalização, matching, validação e quiet hours."""
from datetime import datetime

from core.matching import matches, offer_hash
from core.text import normalize
from core.timeutils import in_quiet_hours
from core.validation import validate_keyword


def test_normalize_accents_and_case():
    assert normalize("Promoção  RTX") == "promocao rtx"
    assert normalize("  Air   Fryer ") == "air fryer"


def test_matches_word_boundary():
    assert matches(normalize("RTX 4070 Ti por R$3000"), normalize("rtx 4070"))
    assert not matches(normalize("promocao do dia"), normalize("moc"))


def test_matches_phrase():
    assert matches(normalize("Air Fryer Mondial 4L"), normalize("air fryer"))
    assert not matches(normalize("fryer de ar"), normalize("air fryer"))


def test_matches_words_with_extra_words_in_between():
    post = normalize("Placa-Mãe AMD AM4 Asus TUF GAMING B550M-Plus - 90MB14A0-C1BAY0")
    assert matches(post, normalize("Asus Tuf B550M-PLUS"))
    assert matches(post, normalize("b550m plus asus"))
    assert not matches(post, normalize("asus tuf b550m-plus wifi"))


def test_matches_requires_words_close_together():
    post = normalize(
        "Projetor Smart Portátil Full Hd 4k Android 13 Wi-fi "
        "Bluetooth Tv Mini Projetor Hy300 Home Theater"
    )
    assert not matches(post, normalize("Smart TV"))
    assert matches(normalize("TV Smart Samsung 50 4K"), normalize("smart tv"))
    assert matches(normalize("Smart TV LG 50"), normalize("smart tv"))


def test_matches_empty():
    assert not matches("", "rtx")
    assert not matches("rtx 4070", "")


def test_validate_keyword():
    assert validate_keyword("a")[0] is None            # muito curta
    assert validate_keyword("x" * 61)[0] is None       # muito longa
    assert validate_keyword("um dois tres quatro cinco seis sete")[0] is None  # tokens demais
    term, err = validate_keyword("RTX 4070")
    assert term == "rtx 4070" and err is None


def test_offer_hash_is_stable_after_normalization():
    a = offer_hash("RTX 4070  ", "PromoBR")
    b = offer_hash("rtx 4070", "promobr")
    assert a == b
    assert offer_hash("rtx 4070", "outro") != a  # canal diferente => hash diferente


def test_quiet_hours_overnight_range():
    assert in_quiet_hours("23:00", "07:00", datetime(2026, 1, 1, 2, 0))
    assert not in_quiet_hours("23:00", "07:00", datetime(2026, 1, 1, 12, 0))


def test_quiet_hours_same_day_range():
    assert in_quiet_hours("00:00", "08:00", datetime(2026, 1, 1, 3, 0))
    assert not in_quiet_hours("00:00", "08:00", datetime(2026, 1, 1, 9, 0))


def test_quiet_hours_disabled():
    assert not in_quiet_hours(None, None, datetime(2026, 1, 1, 3, 0))
