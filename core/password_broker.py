from __future__ import annotations

import asyncio


class PasswordBroker:
    """
    Bridges an async "wait for password" call inside a processor with the
    password_response message that arrives later on the bridge group.

    Usage:
        waiter = password_broker.create_waiter(job.job_id)
        await telegram_service.send_password_request(...)
        password = await asyncio.wait_for(waiter, timeout=300)

    And, wherever bridge messages are read:
        password_broker.resolve(job_id, password)
    """

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}

    def create_waiter(self, job_id: str) -> asyncio.Future:
        future = asyncio.get_event_loop().create_future()
        self._pending[job_id] = future
        return future

    def resolve(self, job_id: str | None, password: str) -> bool:
        if not job_id:
            return False

        future = self._pending.pop(job_id, None)

        if future is not None and not future.done():
            future.set_result(password)
            return True

        return False

    def cancel(self, job_id: str):
        future = self._pending.pop(job_id, None)

        if future is not None and not future.done():
            future.cancel()


password_broker = PasswordBroker()
