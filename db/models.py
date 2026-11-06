"""Modelos SQLAlchemy 2.0 (esquema da seção 4.1 da spec)."""
from typing import Optional

from sqlalchemy import Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1)       # 0 = bloqueou o bot
    is_paused: Mapped[int] = mapped_column(Integer, default=0)
    quiet_start: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # "00:00"
    quiet_end: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)    # "08:00"
    created_at: Mapped[str] = mapped_column(String(40))


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    term: Mapped[str] = mapped_column(String(80), index=True)   # normalizado
    raw_term: Mapped[str] = mapped_column(String(80))           # como o usuário digitou
    max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # None = sem limite
    created_at: Mapped[str] = mapped_column(String(40))

    __table_args__ = (UniqueConstraint("user_id", "term", name="uq_user_term"),)


class Source(Base):
    """Canal monitorado por UM usuário, lido com a conta dele."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(128))  # username sem @, lowercase
    title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | error | removed
    # 1 = o bot fez a conta entrar no canal (sai ao remover); 0 = o usuário já seguia (não sai)
    joined_by_bot: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[str] = mapped_column(String(40))

    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_user_channel"),)


class Account(Base):
    """Conta do Telegram conectada por um usuário via /login."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    tg_user_id: Mapped[int] = mapped_column(Integer)
    phone_masked: Mapped[str] = mapped_column(String(32))          # nunca o número completo
    session_enc: Mapped[str] = mapped_column(Text)                  # StringSession criptografada
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    created_at: Mapped[str] = mapped_column(String(40))


class SentAlert(Base):
    __tablename__ = "sent_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_channel: Mapped[str] = mapped_column(String(128))
    message_link: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    sent_at: Mapped[str] = mapped_column(String(40))

    __table_args__ = (UniqueConstraint("user_id", "offer_hash", name="uq_user_offer"),)
