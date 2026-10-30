"""`first_error_or_done`, and caller cancellation: `asyncio.wait` does not
cancel its children when the awaiting task is cancelled, `asyncio.gather` does."""

import asyncio

import pytest

from kiln_harness_tuner.concurrency import first_error_or_done


async def _hang(cancelled: list[str], name: str) -> None:
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        cancelled.append(name)
        raise


async def test_caller_cancellation_cancels_every_child():
    cancelled: list[str] = []
    tasks = [
        asyncio.ensure_future(_hang(cancelled, "a")),
        asyncio.ensure_future(_hang(cancelled, "b")),
    ]
    waiter = asyncio.ensure_future(first_error_or_done(tasks))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert sorted(cancelled) == ["a", "b"]
    assert all(t.cancelled() for t in tasks)


async def test_first_raise_cancels_the_siblings_and_returns_it():
    cancelled: list[str] = []

    async def boom() -> None:
        raise RuntimeError("fatal")

    tasks = [
        asyncio.ensure_future(_hang(cancelled, "a")),
        asyncio.ensure_future(boom()),
    ]
    error, pending = await first_error_or_done(tasks)
    assert isinstance(error, RuntimeError) and pending == set()
    assert cancelled == ["a"]


async def test_timeout_leaves_the_work_in_flight_for_the_caller():
    cancelled: list[str] = []
    tasks = [asyncio.ensure_future(_hang(cancelled, "a"))]
    error, pending = await first_error_or_done(tasks, timeout=0.01)
    assert error is None and pending == set(tasks)
    assert cancelled == []  # the caller decides
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
