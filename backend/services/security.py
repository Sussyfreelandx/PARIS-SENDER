"""Central encryption helpers for sensitive application data."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import ClassVar

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecurityService:
    """Encrypt and decrypt secrets with Fernet, supporting key rotation."""

    ENV_KEY = "PARIS_SECRET_KEY"
    ENV_KEYS = "PARIS_SECRET_KEYS"
    KEYRING_SERVICE = "paris-sender"
    KEYRING_USER = "fernet-keys"
    TOKEN_PREFIX = "fernet:v1:"
    _fallback_keys: ClassVar[list[bytes] | None] = None

    def __init__(self, keys: list[str | bytes] | None = None) -> None:
        self._keys = self._load_keys(keys)
        self._fernet = MultiFernet([Fernet(key) for key in self._keys])

    def encrypt(self, data: str | bytes) -> str:
        """Encrypt text or bytes and return an opaque token string."""
        payload = data.encode("utf-8") if isinstance(data, str) else data
        return f"{self.TOKEN_PREFIX}{self._fernet.encrypt(payload).decode('ascii')}"

    def decrypt(self, token: str) -> str:
        """Decrypt a Fernet token and return UTF-8 text."""
        raw = token[len(self.TOKEN_PREFIX) :] if token.startswith(self.TOKEN_PREFIX) else token
        return self._fernet.decrypt(raw.encode("ascii")).decode("utf-8")

    def rotate_key(self) -> str:
        """Generate a new primary key while retaining previous keys for decryption."""
        new_key = Fernet.generate_key()
        self._keys.insert(0, new_key)
        self._fernet = MultiFernet([Fernet(key) for key in self._keys])
        self._store_keyring_keys(self._keys)
        return new_key.decode("ascii")

    def is_encrypted(self, value: str | None) -> bool:
        """Return True when the value uses this service's token prefix."""
        return bool(value and value.startswith(self.TOKEN_PREFIX))

    def _load_keys(self, keys: list[str | bytes] | None) -> list[bytes]:
        candidates = keys or self._environment_keys() or self._keyring_keys() or self._generated_keyring_key() or self._fallback_key()
        return [self._normalize_key(candidate) for candidate in candidates]

    def _environment_keys(self) -> list[str]:
        values: list[str] = []
        if os.environ.get(self.ENV_KEY):
            values.append(os.environ[self.ENV_KEY])
        if os.environ.get(self.ENV_KEYS):
            values.extend(part.strip() for part in os.environ[self.ENV_KEYS].split(",") if part.strip())
        return values

    def _keyring_keys(self) -> list[str]:
        try:
            import keyring

            stored = keyring.get_password(self.KEYRING_SERVICE, self.KEYRING_USER)
        except Exception:
            return []
        return [part.strip() for part in (stored or "").split(",") if part.strip()]

    def _store_keyring_keys(self, keys: list[bytes]) -> None:
        try:
            import keyring

            keyring.set_password(self.KEYRING_SERVICE, self.KEYRING_USER, ",".join(key.decode("ascii") for key in keys))
        except Exception:
            return

    def _generated_keyring_key(self) -> list[bytes]:
        key = Fernet.generate_key()
        try:
            import keyring

            keyring.set_password(self.KEYRING_SERVICE, self.KEYRING_USER, key.decode("ascii"))
            return [key]
        except Exception:
            return []

    @classmethod
    def _fallback_key(cls) -> list[bytes]:
        if cls._fallback_keys is None:
            cls._fallback_keys = [Fernet.generate_key()]
        return cls._fallback_keys

    def _normalize_key(self, value: str | bytes) -> bytes:
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        try:
            Fernet(raw)
            return raw
        except (ValueError, TypeError):
            digest = hashlib.sha256(raw).digest()
            return base64.urlsafe_b64encode(digest)

    def decrypt_or_plaintext(self, value: str | None) -> str | None:
        """Decrypt encrypted values, leaving legacy plaintext unchanged."""
        if value is None:
            return None
        if not self.is_encrypted(value):
            return value
        try:
            return self.decrypt(value)
        except (InvalidToken, ValueError):
            return value
