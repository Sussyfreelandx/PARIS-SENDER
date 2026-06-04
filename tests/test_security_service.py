"""Tests for central secret encryption helpers."""

from __future__ import annotations

from backend.services import SecurityService


def test_security_service_encrypts_decrypts_and_rotates() -> None:
    service = SecurityService(keys=["primary-test-key"])
    token = service.encrypt("secret text")

    assert token.startswith(SecurityService.TOKEN_PREFIX)
    assert "secret text" not in token
    assert service.decrypt(token) == "secret text"

    service.rotate_key()
    rotated = service.encrypt(b"new secret")
    assert service.decrypt(token) == "secret text"
    assert service.decrypt(rotated) == "new secret"


def test_decrypt_or_plaintext_preserves_legacy_values() -> None:
    service = SecurityService(keys=["primary-test-key"])

    assert service.decrypt_or_plaintext("legacy pem") == "legacy pem"
    assert service.decrypt_or_plaintext(None) is None
