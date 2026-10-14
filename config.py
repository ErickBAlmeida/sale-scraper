"""Carrega e valida a configuração a partir de variáveis de ambiente (.env)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Variável de ambiente obrigatória ausente: {name}. "
            f"Preencha o arquivo .env (veja .env.example)."
        )
    return value


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_id: int
    api_hash: str
    owner_id: int
    db_path: str
    session_name: str
    log_level: str
    timezone: str
    max_keywords: int
    dedup_window_hours: int


def load_config() -> Config:
    return Config(
        bot_token=_require("BOT_TOKEN"),
        api_id=int(_require("TELEGRAM_API_ID")),
        api_hash=_require("TELEGRAM_API_HASH"),
        owner_id=int(_require("OWNER_ID")),
        db_path=os.getenv("DB_PATH", "data/sale_scraper.sqlite"),
        session_name=os.getenv("SESSION_NAME", "data/userbot"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        timezone=os.getenv("TIMEZONE", "America/Sao_Paulo"),
        max_keywords=int(os.getenv("MAX_KEYWORDS", "10")),
        dedup_window_hours=int(os.getenv("DEDUP_WINDOW_HOURS", "24")),
    )
