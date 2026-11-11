"""Detecção de ofertas repetidas entre canais diferentes.

Vários canais repostam a mesma promoção com textos diferentes (link de
afiliado, emojis, rodapé próprio), então o hash do texto não as reconhece.
Aqui a oferta vira uma "impressão digital" (palavra-chave que casou, preço e
IDs de produto encontrados nas URLs) que é comparada com os alertas recentes
do usuário.
"""
import re
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional

# (prefixo, regex com o ID do produto no grupo 1 [e 2]). Só URLs já expandidas:
# links encurtados (amzn.to, /sec/...) não têm o ID e caem na regra de preço.
_PRODUCT_PATTERNS = [
    ("amazon", re.compile(r"amazon\.[a-z.]+/(?:\S*?/)?(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", re.I)),
    ("mlb", re.compile(r"mercadolivre\.com\.br/\S*?MLB-?(\d{6,})", re.I)),
    ("shopee", re.compile(r"shopee\.com\.br\S*?(?:-i\.|/product/)(\d+)[./](\d+)", re.I)),
    ("magalu", re.compile(r"(?:magazineluiza|magazinevoce)\.com\.br/\S*?/p/([a-z0-9]{6,})", re.I)),
    ("kabum", re.compile(r"kabum\.com\.br/produto/(\d+)", re.I)),
    ("aliexpress", re.compile(r"aliexpress\.[a-z.]+/item/(\d+)", re.I)),
]

_PRICE_TOLERANCE = 0.01


def extract_product_keys(text: str) -> FrozenSet[str]:
    """IDs de produto das URLs da oferta, ex.: {"amazon:B0CXYZ1234", "mlb:123456789"}."""
    if not text:
        return frozenset()
    keys = set()
    for prefix, pattern in _PRODUCT_PATTERNS:
        for m in pattern.finditer(text):
            ident = ".".join(g for g in m.groups() if g)
            keys.add(f"{prefix}:{ident.upper() if prefix == 'amazon' else ident.lower()}")
    return frozenset(keys)


@dataclass(frozen=True)
class OfferFingerprint:
    term: str                       # palavra-chave (normalizada) que casou
    price: Optional[float]          # None = post sem preço
    product_keys: FrozenSet[str] = field(default_factory=frozenset)


def _same_price(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) < _PRICE_TOLERANCE


def _is_same_offer(new: OfferFingerprint, old: OfferFingerprint) -> bool:
    # Preço conhecido e diferente = oferta nova (ex.: o preço caiu), mesmo que seja o mesmo produto.
    if new.price is not None and old.price is not None and not _same_price(new.price, old.price):
        return False
    # Mesmo produto na loja (preço igual ou ausente em um dos posts).
    if new.product_keys & old.product_keys:
        return True
    # Mesma palavra-chave pelo mesmo preço.
    return new.term == old.term and _same_price(new.price, old.price)


def is_duplicate(new: OfferFingerprint, recent: Iterable[OfferFingerprint]) -> bool:
    """True se `new` é a mesma oferta de algum alerta recente do usuário."""
    return any(_is_same_offer(new, old) for old in recent)
