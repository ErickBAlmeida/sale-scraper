"""Ponto de entrada: sobe o bot de notificação (PTB) e as contas dos usuários
(Telethon, uma por usuário, via ClientManager) no mesmo event loop asyncio.

Cada usuário conecta a própria conta pelo /login no bot. Não há mais login
interativo no terminal: a sessão antiga do admin (SESSION_NAME.session), se
existir, é migrada automaticamente para a conta dele na primeira execução.
"""
import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler

from telegram.ext import Application
from telethon.sessions import SQLiteSession, StringSession

from bot.handlers import register_handlers
from config import load_config
from core.crypto import SessionCipher
from db.database import Database
from listener.manager import ClientManager


def migrate_admin_session(cfg, db: Database, cipher: SessionCipher, log) -> None:
    """Converte o arquivo de sessão do userbot antigo em conta do admin (uma vez só).

    Conversão offline (só lê o arquivo, sem conectar): conectar aqui abriria uma
    segunda conexão com a mesma chave, o que pode fazer o Telegram invalidá-la.
    A validade da sessão é conferida depois, pelo ClientManager.
    """
    session_file = cfg.session_name + ".session"
    if not os.path.exists(session_file):
        return
    admin = db.get_or_create_user(cfg.owner_id, None)
    if db.get_account(admin.id) is not None:
        return
    old = SQLiteSession(cfg.session_name)
    try:
        if old.auth_key is None:
            log.warning("Sessão antiga %s sem autorização; admin precisa usar /login", session_file)
            return
        session_str = StringSession.save(old)
    finally:
        old.close()
    db.save_account(admin.id, cfg.owner_id, "conta migrada", cipher.encrypt(session_str))
    log.info("Migração: sessão antiga (%s) virou a conta do admin", session_file)


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
    db.init(owner_chat_id=cfg.owner_id)
    cipher = SessionCipher(cfg.session_key)
    migrate_admin_session(cfg, db, cipher, log)

    application = Application.builder().token(cfg.bot_token).build()
    manager = ClientManager(db, cfg, application.bot, cipher)
    register_handlers(application, db, cfg, manager, cipher)

    # Bot de notificação.
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    # Contas dos usuários (uma conexão por conta).
    await manager.start_all()
    log.info("✅ Bot e listeners em execução.")
    try:
        await application.bot.send_message(cfg.owner_id, "✅ Sale Scraper iniciado.")
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
        # Avisa o dono antes de desligar o bot (só funciona em encerramento normal:
        # ⏹ no Docker, `docker compose stop/down`, Ctrl+C). Queda de energia ou
        # processo morto à força não passam por aqui.
        try:
            await asyncio.wait_for(
                application.bot.send_message(
                    cfg.owner_id,
                    "🔴 Sale Scraper encerrado. Alertas pausados até o bot voltar.",
                ),
                timeout=10,
            )
            log.info("Aviso de encerramento enviado ao dono.")
        except Exception as e:  # noqa: BLE001
            log.warning("Não consegui avisar o encerramento: %s", e)
        for shutdown in (
            application.updater.stop,
            application.stop,
            application.shutdown,
            manager.stop_all,
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
