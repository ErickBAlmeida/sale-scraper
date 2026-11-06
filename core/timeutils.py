"""Utilidades de data/hora com timezone (para timestamps e quiet hours)."""
from datetime import datetime
from zoneinfo import ZoneInfo


def now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def now_iso(tz: str) -> str:
    return now(tz).isoformat()


def format_duration(seconds: float) -> str:
    """Duração legível: 42 -> "42s" | 125 -> "2min" | 7500 -> "2h 05min" | 93784 -> "1d 2h 03min"."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes:02d}min"
    if hours:
        return f"{hours}h {minutes:02d}min"
    return f"{minutes}min"


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
