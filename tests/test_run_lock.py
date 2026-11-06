"""The one-run-per-task lock (`kiln_harness_tuner.run_lock`).

The tests that take a real lock run on every OS in CI: `flock` on macOS and
Linux, `msvcrt.locking` on Windows. `TestWindowsBackend` drives the Windows
code on any OS through a fake `msvcrt`."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from platform_marks import posix_only

from kiln_harness_tuner import run_lock
from kiln_harness_tuner.run_lock import RunLockError, acquire_run_lock

# A child that takes the lock, says so with its own pid (on Windows a venv's
# `python.exe` is a launcher, so the pid `Popen` sees is not the
# interpreter's), and then waits to be killed.
HOLDER = """
import os, sys, time
from pathlib import Path
from kiln_harness_tuner.run_lock import acquire_run_lock
acquire_run_lock(Path(sys.argv[1]), "t1", "p1")
print(os.getpid(), flush=True)
time.sleep(120)
"""


def _acquire_within(lock_dir: Path, seconds: float) -> run_lock.RunLock:
    """Windows frees a dead process's locks soon after it ends, not at once."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return acquire_run_lock(lock_dir, "t1", "p1")
        except RunLockError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def test_the_lock_writes_a_record_and_is_released_not_unlinked(tmp_path):
    lock = acquire_run_lock(tmp_path, "t1")
    record = json.loads(lock.path.read_text(encoding="utf-8"))
    assert record["pid"] == os.getpid()
    assert record["task_id"] == "t1" and "started" in record
    lock.release()
    lock.release()  # a second release is a no-op
    # Released, not unlinked: the file is the lock's identity; a fresh inode
    # under the same name would split the lock.
    assert lock.path.exists()
    again = acquire_run_lock(tmp_path, "t1")
    assert again.path == lock.path
    again.release()


def test_the_lock_key_carries_the_project_id_when_known(tmp_path):
    # The same task id can exist in two projects; a lock keyed by task
    # alone would block or miss across them.
    lock = acquire_run_lock(tmp_path, "t1", "p9")
    try:
        assert lock.path.name == ".lock-p9-t1"
        assert json.loads(lock.path.read_text(encoding="utf-8"))["project_id"] == "p9"
        # A different project's task of the same id is not blocked.
        acquire_run_lock(tmp_path, "t1", "p8").release()
    finally:
        lock.release()


def test_a_second_run_on_the_same_task_is_refused(tmp_path):
    lock = acquire_run_lock(tmp_path, "t1", "p1")
    try:
        with pytest.raises(RunLockError, match="one run per task") as raised:
            acquire_run_lock(tmp_path, "t1", "p1")
        assert f"pid {os.getpid()}" in str(raised.value)
    finally:
        lock.release()
    acquire_run_lock(tmp_path, "t1", "p1").release()


def test_a_held_os_lock_refuses_before_its_record_is_written(tmp_path):
    # The OS lock is the claim, whatever the file says: here the holder has
    # the lock but has not written its record yet, and the lock must not be
    # reclaimed.
    path = tmp_path / ".lock-t1"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    assert run_lock._backend().try_lock(fd)
    try:
        with pytest.raises(RunLockError, match=r"pid \?"):
            acquire_run_lock(tmp_path, "t1")
    finally:
        os.close(fd)  # closing releases it: no reclaim step
    acquire_run_lock(tmp_path, "t1").release()


def test_a_crashed_holder_frees_the_lock(tmp_path):
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert holder.stdout is not None
        holder_pid = int(holder.stdout.readline())
        with pytest.raises(RunLockError, match=f"pid {holder_pid}"):
            acquire_run_lock(tmp_path, "t1", "p1")
        holder.kill()  # SIGKILL or TerminateProcess: no cleanup code runs
        holder.wait(timeout=30)
        lock = _acquire_within(tmp_path, seconds=10)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)
        if holder.stdout is not None:
            holder.stdout.close()
    assert json.loads(lock.path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()


@posix_only(
    "an older kiln-harness-tuner held the lock by a pid record alone, and"
    " only on macOS and Linux: it could not start on Windows"
)
def test_a_live_older_holder_refuses_a_second_run(tmp_path):
    # An older kiln-harness-tuner wrote a bare live pid and holds no OS lock:
    # still honoured.
    (tmp_path / ".lock-t1").write_text(str(os.getppid()), encoding="utf-8")
    with pytest.raises(RunLockError, match="one run per task"):
        acquire_run_lock(tmp_path, "t1")


def test_a_stale_pid_record_is_reclaimed(tmp_path):
    # A pid that cannot be alive (max pid is far below this on any host).
    (tmp_path / ".lock-t1").write_text("99999999", encoding="utf-8")
    lock = acquire_run_lock(tmp_path, "t1")
    assert json.loads(lock.path.read_text(encoding="utf-8"))["task_id"] == "t1"
    lock.release()


def test_a_garbage_record_is_reclaimed(tmp_path):
    (tmp_path / ".lock-t1").write_bytes(b"\xff\xfenot-a-pid")
    acquire_run_lock(tmp_path, "t1").release()


class FakeMsvcrt:
    """`msvcrt.locking` for one process: byte-range locks keyed by file and
    offset, held per descriptor, with the real module's constants."""

    LK_UNLCK = 0
    LK_NBLCK = 2

    def __init__(self) -> None:
        self.held: dict[tuple[int, int, int], int] = {}
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        offset = os.lseek(fd, 0, os.SEEK_CUR)
        stat = os.fstat(fd)
        key = (stat.st_dev, stat.st_ino, offset)
        self.calls.append((mode, offset, nbytes))
        if mode == self.LK_NBLCK:
            if key in self.held:
                raise OSError(errno.EACCES, "Permission denied")
            self.held[key] = fd
        elif mode == self.LK_UNLCK:
            self.held.pop(key, None)


class TestWindowsBackend:
    @pytest.fixture
    def msvcrt(self, monkeypatch):
        fake = FakeMsvcrt()
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        monkeypatch.setattr(sys, "platform", "win32")
        return fake

    def test_windows_locks_one_byte_past_the_record(self, tmp_path, msvcrt):
        lock = acquire_run_lock(tmp_path, "t1")
        assert isinstance(lock._backend, run_lock._MsvcrtBackend)
        offset = run_lock._MsvcrtBackend.OFFSET
        assert msvcrt.calls == [(msvcrt.LK_NBLCK, offset, 1)]
        # The record sits before the locked byte, so a refused run reads it,
        # and the lock past the end does not grow the file.
        assert lock.path.stat().st_size < offset
        with pytest.raises(RunLockError, match=f"pid {os.getpid()}"):
            acquire_run_lock(tmp_path, "t1")
        lock.release()
        assert msvcrt.calls[-1] == (msvcrt.LK_UNLCK, offset, 1)
        acquire_run_lock(tmp_path, "t1").release()

    def test_windows_never_probes_a_pid_with_os_kill(
        self, tmp_path, msvcrt, monkeypatch
    ):
        # On Windows `os.kill(pid, 0)` sends a Ctrl-C event or ends the
        # process. A record that names a live pid, with no lock held, is a
        # dead run's leftover there: no older tuner ran on Windows.
        def no_kill(*_args):
            raise AssertionError("os.kill must not be called on Windows")

        monkeypatch.setattr(os, "kill", no_kill)
        (tmp_path / ".lock-t1").write_text(str(os.getppid()), encoding="utf-8")
        acquire_run_lock(tmp_path, "t1").release()

    def test_a_windows_lock_error_that_is_not_a_conflict_is_raised(
        self, tmp_path, msvcrt, monkeypatch
    ):
        def bad_descriptor(*_args):
            raise OSError(errno.EBADF, "Bad file descriptor")

        monkeypatch.setattr(msvcrt, "locking", bad_descriptor)
        with pytest.raises(OSError) as raised:
            acquire_run_lock(tmp_path, "t1")
        assert raised.value.errno == errno.EBADF
