"""Dublês de Telegram (Bot API e Telethon) para testar sem rede."""
from types import SimpleNamespace

from telethon.errors import (
    AuthKeyUnregisteredError,
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

TZ = "America/Sao_Paulo"


class FakeBot:
    def __init__(self):
        self.sent = []  # (chat_id, text)

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self.sent.append((chat_id, text))


class FakeTelethon:
    """Imita o TelegramClient do Telethon no que o bot usa."""

    def __init__(self, me_id=1, code="12345", password=None, revoked=False, session="SESS"):
        self.me_id = me_id
        self.code = code
        self.password = password
        self.revoked = revoked
        self.connected = False
        self.logged_out = False
        self.authorized = False
        self.handlers = []
        self.joined = []
        self.left = []
        self.session = SimpleNamespace(save=lambda: session)
        self.entities = {}  # username -> SimpleNamespace(title, left)

    async def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def log_out(self):
        self.logged_out = True
        self.connected = False

    async def send_code_request(self, phone):
        self.phone = phone
        return SimpleNamespace(phone_code_hash="HASH")

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        if password is None:
            if code != self.code:
                raise PhoneCodeInvalidError(request=None)
            if self.password:
                raise SessionPasswordNeededError(request=None)
        elif password != self.password:
            raise PasswordHashInvalidError(request=None)
        self.authorized = True

    async def get_me(self):
        return SimpleNamespace(id=self.me_id)

    def add_event_handler(self, handler, event):
        self.handlers.append(handler)

    async def get_entity(self, channel):
        return self.entities.get(channel) or SimpleNamespace(title=channel.title(), left=True)

    async def __call__(self, request):
        if self.revoked:
            raise AuthKeyUnregisteredError(request=None)
        name = type(request).__name__
        if name == "JoinChannelRequest":
            self.joined.append(request)
        elif name == "LeaveChannelRequest":
            self.left.append(request)
        return None


def fake_config(**over):
    base = dict(
        owner_id=999, timezone=TZ, api_id=1, api_hash="x",
        max_accounts=20, max_sources=20, max_keywords=10, joins_per_hour=5,
    )
    base.update(over)
    return SimpleNamespace(**base)


def fake_event(username, text, msg_id=1, is_channel=True):
    async def get_chat():
        return SimpleNamespace(username=username)

    return SimpleNamespace(
        is_channel=is_channel,
        is_group=False,
        get_chat=get_chat,
        raw_text=text,
        message=SimpleNamespace(id=msg_id),
    )
