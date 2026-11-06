"""Criptografia das sessões das contas dos usuários (Fernet / AES + HMAC).

A sessão do Telethon dá acesso total à conta do usuário, então nunca fica em
texto puro no banco. A chave vem de SESSION_KEY no .env; perder a chave obriga
todos os usuários a refazerem /login.

Gerar uma chave:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from cryptography.fernet import Fernet, InvalidToken


class SessionCipher:
    def __init__(self, key: str):
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as e:
            raise ValueError(
                "SESSION_KEY inválida. Gere uma com: python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\""
            ) from e

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Levanta ValueError se o dado não foi criptografado com esta chave."""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise ValueError("Sessão não pôde ser descriptografada (SESSION_KEY trocada?)") from e


def mask_phone(phone: str) -> str:
    """'5511999994321' -> '+55•••••4321' (nunca guardar/logar o número completo)."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) <= 6:
        return "+" + "•" * len(digits)
    return f"+{digits[:2]}{'•' * (len(digits) - 6)}{digits[-4:]}"
