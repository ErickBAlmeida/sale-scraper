"""Modelos SQLAlchemy 2.0 (esquema da seção 4.1 da spec)."""
from typing import Optional

from sqlalchemy import Integer, String, UniqueConstraint
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
    created_at: Mapped[str] = mapped_column(String(40))

    __table_args__ = (UniqueConstraint("user_id", "term", name="uq_user_term"),)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(128), unique=True)  # username sem @, lowercase
    title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | error | removed
    added_at: Mapped[str] = mapped_column(String(40))


class SentAlert(Base):
    __tablename__ = "sent_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_channel: Mapped[str] = mapped_column(String(128))
    message_link: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    sent_at: Mapped[str] = mapped_column(String(40))

    __table_args__ = (UniqueConstraint("user_id", "offer_hash", name="uq_user_offer"),)
