from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from cryptography.fernet import Fernet

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


class TokenCipher:
    """Wraps Fernet so refresh tokens are never stored in plain text in the DB."""

    _fernet = None

    @classmethod
    def init(cls, key: str):
        cls._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @classmethod
    def encrypt(cls, plain_text: str) -> str:
        if not plain_text:
            return plain_text
        return cls._fernet.encrypt(plain_text.encode()).decode()

    @classmethod
    def decrypt(cls, cipher_text: str) -> str:
        if not cipher_text:
            return cipher_text
        return cls._fernet.decrypt(cipher_text.encode()).decode()
