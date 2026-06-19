"""Unit tests for password hashing + JWT helpers (app/core/security.py). Pure, no DB."""

from datetime import timedelta

import pytest
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_then_verify_roundtrip(self):
        h = get_password_hash("s3cret-pass")
        assert verify_password("s3cret-pass", h) is True

    def test_wrong_password_fails(self):
        h = get_password_hash("correct")
        assert verify_password("wrong", h) is False

    def test_hash_is_salted_so_two_hashes_differ(self):
        assert get_password_hash("same") != get_password_hash("same")

    def test_hash_is_not_plaintext(self):
        assert get_password_hash("plaintext") != "plaintext"

    def test_verify_with_garbage_hash_returns_false(self):
        # bcrypt.checkpw raises ValueError on a malformed hash; we catch -> False.
        assert verify_password("x", "not-a-real-bcrypt-hash") is False


class TestAccessToken:
    def test_creates_decodable_access_token(self):
        token = create_access_token("user-123")
        claims = decode_token(token)
        assert claims["sub"] == "user-123"
        assert claims["type"] == "access"

    def test_extra_claims_are_embedded(self):
        token = create_access_token("u", extra_claims={"role": "ADMIN", "company": "c1"})
        claims = decode_token(token)
        assert claims["role"] == "ADMIN"
        assert claims["company"] == "c1"

    def test_subject_is_stringified(self):
        token = create_access_token(12345)
        assert decode_token(token)["sub"] == "12345"

    def test_expired_token_raises(self):
        token = create_access_token("u", expires_delta=timedelta(seconds=-5))
        with pytest.raises(ExpiredSignatureError):
            decode_token(token)


class TestRefreshToken:
    def test_creates_refresh_token(self):
        claims = decode_token(create_refresh_token("user-9"))
        assert claims["sub"] == "user-9"
        assert claims["type"] == "refresh"


class TestDecodeToken:
    def test_garbage_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("not.a.jwt")

    def test_wrong_signature_raises(self):
        forged = jwt.encode({"sub": "x", "type": "access"}, "the-wrong-key", algorithm="HS256")
        with pytest.raises(JWTError):
            decode_token(forged)
