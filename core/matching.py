"""Motor de correspondência (matching) e hash de deduplicação de ofertas."""
import hashlib
import re

from core.text import normalize


def matches(normalized_text: str, normalized_term: str) -> bool:
    """Correspondência por limite de palavra (word-boundary).

    Evita falsos positivos de substring: 'moc' não casa 'promocao'.
    Frases casam se a sequência aparecer no texto ('air fryer').
    Espera os dois argumentos JÁ normalizados (core.text.normalize).
    """
    if not normalized_text or not normalized_term:
        return False
    pattern = r"\b" + re.escape(normalized_term) + r"\b"
    return re.search(pattern, normalized_text) is not None


def offer_hash(text: str, channel: str) -> str:
    """Hash estável de uma oferta (canal + texto normalizado) para dedup."""
    base = f"{channel.lower()}::{normalize(text)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
