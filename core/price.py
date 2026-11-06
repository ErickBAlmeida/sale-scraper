"""Filtro de preço: leitura do preço máximo no cadastro e do preço da oferta.

Cadastro:  "/add ryzen 5 5500 <500"  ->  item "ryzen 5 5500", máximo R$ 500.
Oferta:    "💳 R$ 546 no PIX com cupom"  ->  546.0
"""
import re
from typing import Optional, Tuple

# Número no formato brasileiro: 500 | 1500 | 1.500 | 1.299,90 | 99,9
_NUMBER = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?"

# Sufixo de preço máximo no cadastro: "<500", "< 500", "<=500", "<R$ 1.500,90"
_MAX_SUFFIX = re.compile(r"\s*<\s*=?\s*(?:r\$\s*)?" + _NUMBER + r"\s*$", re.IGNORECASE)

# Valor em reais dentro de um post: "R$ 546", "R$1.299,90"
_PRICE_IN_TEXT = re.compile(r"r\$\s*" + _NUMBER, re.IGNORECASE)

# Parcela logo antes do valor: "12x de R$ 108", "10x R$ 99" -> não é o preço total
_INSTALLMENT_BEFORE = re.compile(r"\d+\s*x\s*(?:de\s*)?$", re.IGNORECASE)


def _to_float(integer_part: str, cents: Optional[str]) -> float:
    value = float(integer_part.replace(".", ""))
    if cents:
        value += float(cents.ljust(2, "0")) / 100
    return value


def split_max_price(raw: str) -> Tuple[str, Optional[float]]:
    """Separa o item do preço máximo opcional no final do texto.

    "ryzen 5 5500 <500" -> ("ryzen 5 5500", 500.0)
    "ryzen 5 5500"      -> ("ryzen 5 5500", None)
    """
    m = _MAX_SUFFIX.search(raw)
    if not m:
        return raw.strip(), None
    return raw[: m.start()].strip(), _to_float(m.group(1), m.group(2))


def extract_price(text: str) -> Optional[float]:
    """Preço da oferta: o menor valor em R$ do texto, ignorando parcelas.

    O menor valor cobre "De R$ 999 por R$ 546" (o preço é o de "por").
    Retorna None se o post não tiver nenhum valor em R$.
    """
    if not text:
        return None
    prices = []
    for m in _PRICE_IN_TEXT.finditer(text):
        before = text[max(0, m.start() - 15): m.start()]
        if _INSTALLMENT_BEFORE.search(before):
            continue
        value = _to_float(m.group(1), m.group(2))
        if value > 0:
            prices.append(value)
    return min(prices) if prices else None


def within_max(price: Optional[float], max_price: Optional[float]) -> bool:
    """Sem preço máximo, sempre passa. Com preço máximo, exige preço conhecido e <= máximo."""
    if max_price is None:
        return True
    return price is not None and price <= max_price


def format_brl(value: float) -> str:
    """500 -> "R$ 500" | 1500 -> "R$ 1.500" | 1299.9 -> "R$ 1.299,90"."""
    if value == int(value):
        s = f"{int(value):,}".replace(",", ".")
    else:
        s = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {s}"
