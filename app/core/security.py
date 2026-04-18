from datetime import UTC, datetime, timedelta
from typing import cast

import bcrypt
from jose import jwt

from app.core.settings import get_settings

_settings = get_settings()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def create_access_token(
    subject: object, expires_delta: timedelta | None = None, extra_claims: dict[str, object] | None = None
) -> str:
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=_settings.access_token_expire_minutes)

    to_encode: dict[str, object] = {"exp": expire, "sub": str(subject), "type": "access"}
    if extra_claims:
        to_encode.update(extra_claims)

    encoded_jwt = cast("str", jwt.encode(to_encode, _settings.secret_key, algorithm=_settings.algorithm))
    return encoded_jwt


def create_refresh_token(subject: object, expires_delta: timedelta | None = None) -> str:
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        # Refresh tokens usually last longer, e.g. 7 days
        expire = datetime.now(UTC) + timedelta(days=7)

    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh"}
    encoded_jwt = cast("str", jwt.encode(to_encode, _settings.secret_key, algorithm=_settings.algorithm))
    return encoded_jwt


def decode_token(token: str) -> dict[str, object]:
    return cast(
        "dict[str, object]", jwt.decode(token, _settings.secret_key, algorithms=[_settings.algorithm])
    )
