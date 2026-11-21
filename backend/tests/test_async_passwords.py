import asyncio
import threading

import pytest
from libtv_agent.services.auth import AsyncPasswordService, PasswordService


async def test_password_worker_preserves_hash_and_verification_contract():
    service = AsyncPasswordService()
    encoded = await service.hash("synthetic-long-password")
    assert await service.verify(encoded, "synthetic-long-password")
    assert not await service.verify(encoded, "synthetic-wrong-password")
    assert not await service.verify("offline-disabled", "synthetic-long-password")
    with pytest.raises(ValueError):
        await service.hash("short")
    # A failed hash releases capacity for later requests.
    assert await service.verify(encoded, "synthetic-long-password")


async def test_password_work_is_bounded_and_cancelled_callers_keep_their_slot(monkeypatch):
    release = threading.Event()
    two_started = threading.Event()
    lock = threading.Lock()
    calls = []
    event_loop_thread = threading.get_ident()

    def blocked_verify(self, encoded, password):
        assert threading.get_ident() != event_loop_thread
        with lock:
            calls.append(password)
            if len(calls) == 2:
                two_started.set()
        assert release.wait(timeout=5), "test password workers were not released"
        return True

    monkeypatch.setattr(PasswordService, "verify", blocked_verify)
    service = AsyncPasswordService(max_concurrency=2)
    requests = [asyncio.create_task(service.verify("synthetic", str(i))) for i in range(3)]
    try:
        assert await asyncio.wait_for(asyncio.to_thread(two_started.wait, 3), timeout=4)
        # Both expensive calls are blocked, but the event loop still schedules work.
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=1)
        assert calls == ["0", "1"] or calls == ["1", "0"]
        requests[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await requests[0]
        await asyncio.sleep(0.02)
        assert len(calls) == 2
    finally:
        release.set()
        await asyncio.gather(*requests, return_exceptions=True)
    assert await service.verify("synthetic", "after-cancellation")
    assert len(calls) == 4
