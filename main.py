"""Ponto de entrada: sobe o bot de notificação (PTB) e o listener (Telethon)
no mesmo event loop asyncio.

Primeira execução: o Telethon pedirá o número de telefone e o código SMS da
conta do userbot (uma única vez) para criar o arquivo de sessão. Rode-o
interativamente na primeira vez (veja o README).
"""
import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler

from telegram.ext import Application
from telethon import TelegramClient

from bot.handlers import register_handlers
from config import load_config
from db.database import Database
from listener.userbot import Listener


def setup_logging(cfg) -> None:
    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        os.makedirs("data", exist_ok=True)
        fileh = RotatingFileHandler(
            "data/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except Exception:  # noqa: BLE001
        pass

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)


async def run() -> None:
    cfg = load_config()
    setup_logging(cfg)
    log = logging.getLogger("main")

    db = Database(cfg.db_path, cfg.timezone)
    db.init()

    application = Application.builder().token(cfg.bot_token).build()
    client = TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash)
    listener = Listener(client, db, cfg, application.bot)
    register_handlers(application, db, cfg, listener)

    # Userbot: conecta (pode pedir telefone/código na 1ª vez).
    await client.start()
    listener.register()

    # Bot de notificação.
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    await listener.join_all_sources()
    log.info("✅ Bot e listener em execução.")
    try:
        await application.bot.send_message(cfg.owner_id, "✅ sale_scraper iniciado.")
    except Exception:  # noqa: BLE001
        pass

    # Mantém o processo vivo até receber sinal de encerramento.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows não suporta p/ todos os sinais
            pass

    try:
        await stop.wait()
    finally:
        log.info("Encerrando...")
        for shutdown in (
            application.updater.stop,
            application.stop,
            application.shutdown,
            client.disconnect,
        ):
            try:
                await shutdown()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
