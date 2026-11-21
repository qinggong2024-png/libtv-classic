import asyncio
from collections.abc import Callable
from typing import TypeVar

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerifyMismatchError):
            return False


_Result = TypeVar("_Result")


class AsyncPasswordService:
    """Offload Argon2 with a per-app cap, including after caller cancellation."""

    def __init__(self, max_concurrency: int = 2) -> None:
        self._slots = asyncio.Semaphore(max_concurrency)
        self._passwords = PasswordService()

    async def _run(self, operation: Callable[..., _Result], *arguments: str) -> _Result:
        await self._slots.acquire()
        worker = asyncio.create_task(asyncio.to_thread(operation, *arguments))

        def finished(task: asyncio.Task) -> None:
            self._slots.release()
            # Retrieve failures even if the HTTP caller disconnected meanwhile.
            if not task.cancelled():
                task.exception()

        worker.add_done_callback(finished)
        # A cancelled HTTP request cannot stop an Argon2 thread. Keep its slot
        # occupied until the thread finishes so disconnects cannot bypass the cap.
        return await asyncio.shield(worker)

    async def hash(self, password: str) -> str:
        return await self._run(self._passwords.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        return await self._run(self._passwords.verify, password_hash, password)
