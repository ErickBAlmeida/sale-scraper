"""Regras de validação do cadastro de palavras-chave (seção 1.3 da spec)."""
from typing import Optional, Tuple

from core.text import normalize

MIN_LEN = 2
MAX_LEN = 60
MAX_TOKENS = 6


def validate_keyword(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Valida e normaliza uma palavra-chave.

    Retorna (termo_normalizado, None) em caso de sucesso
    ou (None, motivo_do_erro) em caso de falha.
    """
    norm = normalize(raw)
    if len(norm) < MIN_LEN:
        return None, "é muito curta (mínimo de 2 caracteres)."
    if len(norm) > MAX_LEN:
        return None, "é muito longa (máximo de 60 caracteres)."
    if len(norm.split()) > MAX_TOKENS:
        return None, "tem palavras demais (máximo de 6)."
    return norm, None
