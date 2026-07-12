"""Password hashing and signed JWT access-token helpers."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import bcrypt
from jose import JWTError, jwt

from backend.app.core.config import settings

MAX_BCRYPT_PASSWORD_BYTES = 72


class PasswordTooLongError(ValueError):
    """Raised when a UTF-8 password exceeds bcrypt's safe input limit."""


class InvalidAccessTokenError(ValueError):
    """Raised when a token is invalid, expired, or has the wrong claims."""


def password_bytes(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_BCRYPT_PASSWORD_BYTES:
        raise PasswordTooLongError("Password must not exceed 72 UTF-8 bytes")
    return encoded


def hash_password(password: str, *, rounds: int | None = None) -> str:
    encoded = password_bytes(password)
    bcrypt_rounds = settings.password_bcrypt_rounds if rounds is None else rounds
    return bcrypt.hashpw(
        encoded,
        bcrypt.gensalt(rounds=bcrypt_rounds),
    ).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        encoded = password_bytes(password)
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except (PasswordTooLongError, TypeError, UnicodeEncodeError, ValueError):
        return False


def create_access_token(
    user_id: UUID,
    *,
    now: datetime | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    token_lifetime = (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    expires_at = issued_at + token_lifetime
    claims = {
        "sub": str(user_id),
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> UUID:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
        if claims.get("type") != "access":
            raise InvalidAccessTokenError("Token is not an access token")
        return UUID(claims["sub"])
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise InvalidAccessTokenError("Invalid or expired access token") from exc
