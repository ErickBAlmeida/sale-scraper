"""Utilidades de data/hora com timezone (para timestamps e quiet hours)."""
from datetime import datetime
from zoneinfo import ZoneInfo


def now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def now_iso(tz: str) -> str:
    return now(tz).isoformat()


def in_quiet_hours(start, end, current: datetime) -> bool:
    """Retorna True se `current` está dentro da faixa de silêncio.

    start/end no formato "HH:MM" (zero-padded) ou None (silêncio desligado).
    Trata faixas que cruzam a meia-noite (ex.: 23:00–07:00).
    """
    if not start or not end:
        return False
    cur = current.strftime("%H:%M")
    if start <= end:
        return start <= cur < end
    # Faixa atravessa a meia-noite.
    return cur >= start or cur < end
