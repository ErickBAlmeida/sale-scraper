"""Repositório de acesso a dados (SQLite via SQLAlchemy).

Sessões curtas por operação. Retorna DTOs simples (dataclasses) para que
os dados possam ser usados fora do escopo da sessão sem lazy-loading.
"""
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.matching import matches as term_matches
from core.price import split_max_price, within_max
from core.text import normalize
from core.timeutils import now, now_iso
from db.models import Base, Keyword, SentAlert, Source, User

log = logging.getLogger("db")


@dataclass
class UserDTO:
    id: int
    chat_id: int
    username: Optional[str]
    is_active: int
    is_paused: int
    quiet_start: Optional[str]
    quiet_end: Optional[str]


@dataclass
class KeywordDTO:
    id: int
    term: str
    raw_term: str
    max_price: Optional[float] = None


@dataclass
class SourceDTO:
    channel: str
    title: Optional[str]
    status: str


@dataclass
class MatchDTO:
    user_id: int
    chat_id: int
    term: str
    raw_term: str
    quiet_start: Optional[str]
    quiet_end: Optional[str]
    max_price: Optional[float] = None


def _user_dto(u: User) -> UserDTO:
    return UserDTO(u.id, u.chat_id, u.username, u.is_active, u.is_paused, u.quiet_start, u.quiet_end)


class Database:
    def __init__(self, db_path: str, tz: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        self.Session = sessionmaker(self.engine, future=True, expire_on_commit=False)
        self.tz = tz

    def init(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate_max_price()

    def _migrate_max_price(self) -> None:
        """Bancos criados antes do filtro de preço: cria a coluna keywords.max_price
        e converte palavras salvas como "item <500" para item + preço máximo."""
        with self.engine.begin() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(keywords)"))}
            if "max_price" in cols:
                return
            conn.execute(text("ALTER TABLE keywords ADD COLUMN max_price REAL"))
            log.info("Migração: coluna keywords.max_price criada")
            rows = conn.execute(text("SELECT id, user_id, raw_term FROM keywords")).all()
            for kid, uid, raw in rows:
                item, max_price = split_max_price(raw)
                if max_price is None or not item:
                    continue
                term = normalize(item)
                taken = conn.execute(
                    text("SELECT 1 FROM keywords WHERE user_id = :u AND term = :t AND id != :i"),
                    {"u": uid, "t": term, "i": kid},
                ).first()
                if taken:
                    continue  # já existe o mesmo item sem preço; deixa como está
                conn.execute(
                    text("UPDATE keywords SET term = :t, raw_term = :r, max_price = :p WHERE id = :i"),
                    {"t": term, "r": item, "p": max_price, "i": kid},
                )
                log.info("Migração: '%s' -> '%s' (máx. %s)", raw, item, max_price)

    # ---------------- usuários ----------------
    def get_or_create_user(self, chat_id: int, username: Optional[str]) -> UserDTO:
        with self.Session() as s:
            u = s.scalar(select(User).where(User.chat_id == chat_id))
            if u is None:
                u = User(
                    chat_id=chat_id,
                    username=username,
                    is_active=1,
                    is_paused=0,
                    created_at=now_iso(self.tz),
                )
                s.add(u)
                s.commit()
            else:
                changed = False
                if username and u.username != username:
                    u.username = username
                    changed = True
                if not u.is_active:  # reativa quem voltou a interagir
                    u.is_active = 1
                    changed = True
                if changed:
                    s.commit()
            return _user_dto(u)

    def set_paused(self, user_id: int, paused: bool) -> None:
        with self.Session() as s:
            u = s.get(User, user_id)
            if u:
                u.is_paused = 1 if paused else 0
                s.commit()

    def set_quiet(self, user_id: int, start: Optional[str], end: Optional[str]) -> None:
        with self.Session() as s:
            u = s.get(User, user_id)
            if u:
                u.quiet_start = start
                u.quiet_end = end
                s.commit()

    def mark_user_inactive(self, chat_id: int) -> None:
        with self.Session() as s:
            u = s.scalar(select(User).where(User.chat_id == chat_id))
            if u:
                u.is_active = 0
                s.commit()

    def all_active_chat_ids(self) -> List[int]:
        with self.Session() as s:
            return list(s.scalars(select(User.chat_id).where(User.is_active == 1)).all())

    # ---------------- palavras-chave ----------------
    def count_keywords(self, user_id: int) -> int:
        with self.Session() as s:
            return s.scalar(
                select(func.count()).select_from(Keyword).where(Keyword.user_id == user_id)
            ) or 0

    def add_keyword(
        self,
        user_id: int,
        term: str,
        raw: str,
        max_keywords: int,
        max_price: Optional[float] = None,
    ) -> str:
        """Retorna 'ok', 'dup', 'updated' (mesmo item, novo preço máximo) ou 'limit'."""
        with self.Session() as s:
            existing = s.scalar(
                select(Keyword).where(Keyword.user_id == user_id, Keyword.term == term)
            )
            if existing:
                if existing.max_price == max_price:
                    return "dup"
                existing.max_price = max_price
                existing.raw_term = raw
                s.commit()
                return "updated"
            count = s.scalar(
                select(func.count()).select_from(Keyword).where(Keyword.user_id == user_id)
            ) or 0
            if count >= max_keywords:
                return "limit"
            s.add(
                Keyword(
                    user_id=user_id,
                    term=term,
                    raw_term=raw,
                    max_price=max_price,
                    created_at=now_iso(self.tz),
                )
            )
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                return "dup"
            return "ok"

    def list_keywords(self, user_id: int) -> List[KeywordDTO]:
        with self.Session() as s:
            rows = s.scalars(
                select(Keyword).where(Keyword.user_id == user_id).order_by(Keyword.id)
            ).all()
            return [KeywordDTO(k.id, k.term, k.raw_term, k.max_price) for k in rows]

    def remove_keyword(self, user_id: int, arg: str) -> bool:
        """Remove por número (ordem do /list) ou pela própria palavra."""
        kws = self.list_keywords(user_id)
        target: Optional[KeywordDTO] = None
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(kws):
                target = kws[idx]
        if target is None:
            narg = normalize(split_max_price(arg)[0])  # aceita "/remove ryzen 5 5500 <500"
            for k in kws:
                if k.term == narg:
                    target = k
                    break
        if target is None:
            return False
        with self.Session() as s:
            s.execute(delete(Keyword).where(Keyword.id == target.id, Keyword.user_id == user_id))
            s.commit()
        return True

    def clear_keywords(self, user_id: int) -> int:
        with self.Session() as s:
            n = s.scalar(
                select(func.count()).select_from(Keyword).where(Keyword.user_id == user_id)
            ) or 0
            s.execute(delete(Keyword).where(Keyword.user_id == user_id))
            s.commit()
            return n

    # ---------------- matching ----------------
    def find_matches(
        self, normalized_text: str, price: Optional[float] = None
    ) -> List[MatchDTO]:
        """Usuários ativos/não-pausados cuja alguma palavra-chave casa o texto.

        `price` é o preço da oferta (None = post sem preço). Palavras com preço
        máximo só casam se o preço for conhecido e estiver dentro do limite.

        Dedup por usuário: retorna no máximo uma entrada por usuário
        (a primeira palavra que deu match).
        """
        with self.Session() as s:
            rows = s.execute(
                select(
                    Keyword.user_id,
                    Keyword.term,
                    Keyword.raw_term,
                    Keyword.max_price,
                    User.chat_id,
                    User.quiet_start,
                    User.quiet_end,
                )
                .join(User, User.id == Keyword.user_id)
                .where(User.is_active == 1, User.is_paused == 0)
            ).all()
        seen = {}
        for r in rows:
            if r.user_id in seen:
                continue
            if term_matches(normalized_text, r.term) and within_max(price, r.max_price):
                seen[r.user_id] = MatchDTO(
                    r.user_id,
                    r.chat_id,
                    r.term,
                    r.raw_term,
                    r.quiet_start,
                    r.quiet_end,
                    r.max_price,
                )
        return list(seen.values())

    # ---------------- dedup / envio ----------------
    def already_sent(self, user_id: int, offer_hash: str) -> bool:
        with self.Session() as s:
            return s.scalar(
                select(SentAlert.id).where(
                    SentAlert.user_id == user_id, SentAlert.offer_hash == offer_hash
                )
            ) is not None

    def record_sent(self, user_id: int, offer_hash: str, channel: str, link: Optional[str]) -> None:
        with self.Session() as s:
            s.add(
                SentAlert(
                    user_id=user_id,
                    offer_hash=offer_hash,
                    source_channel=channel,
                    message_link=link,
                    sent_at=now_iso(self.tz),
                )
            )
            try:
                s.commit()
            except IntegrityError:
                s.rollback()

    def alert_stats(self, user_id: int):
        with self.Session() as s:
            total = s.scalar(
                select(func.count()).select_from(SentAlert).where(SentAlert.user_id == user_id)
            ) or 0
            today_prefix = now(self.tz).strftime("%Y-%m-%d")
            today = s.scalar(
                select(func.count())
                .select_from(SentAlert)
                .where(SentAlert.user_id == user_id, SentAlert.sent_at.like(today_prefix + "%"))
            ) or 0
            return today, total

    def global_stats(self) -> dict:
        """Totais da aplicação inteira (para o /status do admin)."""
        today_prefix = now(self.tz).strftime("%Y-%m-%d")

        def count(model, *where):
            return s.scalar(select(func.count()).select_from(model).where(*where)) or 0

        with self.Session() as s:
            return {
                "users_active": count(User, User.is_active == 1),
                "users_paused": count(User, User.is_active == 1, User.is_paused == 1),
                "users_inactive": count(User, User.is_active == 0),
                "keywords": count(Keyword),
                "keywords_with_price": count(Keyword, Keyword.max_price.is_not(None)),
                "alerts_today": count(SentAlert, SentAlert.sent_at.like(today_prefix + "%")),
                "alerts_total": count(SentAlert),
                "last_alert_at": s.scalar(select(func.max(SentAlert.sent_at))),
            }

    # ---------------- fontes (canais monitorados) ----------------
    def add_source(self, channel: str, title: Optional[str]) -> None:
        channel = channel.lower()
        with self.Session() as s:
            existing = s.scalar(select(Source).where(Source.channel == channel))
            if existing:
                existing.status = "active"
                if title:
                    existing.title = title
            else:
                s.add(
                    Source(channel=channel, title=title, status="active", added_at=now_iso(self.tz))
                )
            s.commit()

    def remove_source(self, channel: str) -> bool:
        channel = channel.lower()
        with self.Session() as s:
            src = s.scalar(select(Source).where(Source.channel == channel))
            if not src:
                return False
            src.status = "removed"
            s.commit()
            return True

    def list_sources(self, active_only: bool = False) -> List[SourceDTO]:
        with self.Session() as s:
            q = select(Source)
            if active_only:
                q = q.where(Source.status == "active")
            rows = s.scalars(q.order_by(Source.channel)).all()
            return [SourceDTO(x.channel, x.title, x.status) for x in rows]

    def get_active_source_by_username(self, username: Optional[str]) -> Optional[SourceDTO]:
        if not username:
            return None
        with self.Session() as s:
            src = s.scalar(
                select(Source).where(
                    func.lower(Source.channel) == username.lower(), Source.status == "active"
                )
            )
            return SourceDTO(src.channel, src.title, src.status) if src else None
