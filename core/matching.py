"""Motor de correspondência (matching) e hash de deduplicação de ofertas."""
import hashlib
import re
from collections import Counter

from core.text import normalize


_WORD = re.compile(r"\w+")

# Quantas palavras "intrusas" podem aparecer entre as palavras do termo.
MAX_EXTRA_WORDS = 2


def matches(normalized_text: str, normalized_term: str) -> bool:
    """Casa se TODAS as palavras do termo aparecerem PRÓXIMAS no texto, em qualquer ordem.

    Compara palavras inteiras, o que evita falsos positivos de substring:
    'moc' não casa 'promocao'.
    Até MAX_EXTRA_WORDS palavras extras no meio são toleradas:
    'asus tuf b550m-plus' casa 'Asus TUF GAMING B550M-Plus' e 'smart tv' casa
    'TV Smart'. Mas palavras espalhadas pelo post não casam: 'smart tv' NÃO casa
    'Projetor Smart Portátil ... Bluetooth Tv'.
    Hífens e pontuação separam palavras: 'b550m-plus' casa 'B550M Plus'.
    Espera os dois argumentos JÁ normalizados (core.text.normalize).
    """
    if not normalized_text or not normalized_term:
        return False
    needed = Counter(_WORD.findall(normalized_term))
    if not needed:
        return False
    tokens = _WORD.findall(normalized_text)
    window = sum(needed.values()) + MAX_EXTRA_WORDS
    for start, tok in enumerate(tokens):
        if tok in needed and not needed - Counter(tokens[start:start + window]):
            return True
    return False


def offer_hash(text: str, channel: str) -> str:
    """Hash estável de uma oferta (canal + texto normalizado) para dedup."""
    base = f"{channel.lower()}::{normalize(text)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
