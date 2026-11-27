"""Motor de correspondência (matching) e hash de deduplicação de ofertas."""
import hashlib
import re

from core.text import normalize


_WORD = re.compile(r"\w+")


def matches(normalized_text: str, normalized_term: str) -> bool:
    """Casa se TODAS as palavras do termo aparecerem no texto, em qualquer ordem.

    Cada palavra é comparada por limite de palavra (word-boundary), o que evita
    falsos positivos de substring: 'moc' não casa 'promocao'.
    Palavras extras no post não atrapalham: 'asus tuf b550m-plus' casa
    'Asus TUF GAMING B550M-Plus'. Hífens e pontuação separam palavras,
    então 'b550m-plus' também casa 'B550M Plus'.
    Espera os dois argumentos JÁ normalizados (core.text.normalize).
    """
    if not normalized_text or not normalized_term:
        return False
    words = _WORD.findall(normalized_term)
    if not words:
        return False
    return all(
        re.search(r"\b" + re.escape(w) + r"\b", normalized_text) is not None
        for w in words
    )


def offer_hash(text: str, channel: str) -> str:
    """Hash estável de uma oferta (canal + texto normalizado) para dedup."""
    base = f"{channel.lower()}::{normalize(text)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
