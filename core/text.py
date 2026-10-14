"""Normalização de texto usada tanto no cadastro de palavras-chave
quanto na comparação com as mensagens de oferta.

Garante que 'RTX 4070', 'rtx 4070' e 'rtx  4070' sejam equivalentes:
lowercase, remoção de acentos e colapso de espaços.
"""
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    # Decompõe acentos e remove os diacríticos (ç -> c, ã -> a, etc.)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _WHITESPACE.sub(" ", text).strip()
    return text
