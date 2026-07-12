"""User registration and password authentication."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.app.core.security import hash_password, verify_password
from backend.app.models.user import User

# A valid bcrypt hash used to keep missing-user login work close to wrong-password work.
DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.yr4bANZ7T6nZi2I6wF8Wj6E1i6GQK7G"


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, session: AsyncSession, *, bcrypt_rounds: int) -> None:
        self.session = session
        self.bcrypt_rounds = bcrypt_rounds

    async def register(self, *, email: str, password: str) -> User:
        password_hash = await run_in_threadpool(
            hash_password,
            password,
            rounds=self.bcrypt_rounds,
        )
        user = User(
            email=email,
            password_hash=password_hash,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise EmailAlreadyRegisteredError from exc

        await self.session.refresh(user)
        return user

    async def authenticate(self, *, email: str, password: str) -> User:
        user = await self.session.scalar(select(User).where(User.email == email))
        candidate_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_matches = await run_in_threadpool(verify_password, password, candidate_hash)
        if user is None or not password_matches:
            raise InvalidCredentialsError
        return user
