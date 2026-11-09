"""One live run per task, on every OS.

Two concurrent runs on one task race the baseline record and the memory
store, so a second run is refused. The lock is an OS file lock on
`<lock_dir>/.lock-<key>`: `flock` on macOS and Linux, `msvcrt.locking` on
Windows. The OS releases it when the holding process ends, however it ends,
so there is no stale-lock reclaim step and no reclaim race. (The O_EXCL +
unlink scheme before it let two racers each unlink a "stale" file and both
proceed, and a reader that saw the file EMPTY — the holder had created but
not yet written it — reclaimed a live lock.)

The file is created once and never unlinked: unlinking a locked file lets a
third run create a fresh inode under the same name and lock that one while
the second still holds the old inode. The record in it (pid, start time,
ids) is for humans and for the refusal message.

The lock directory is the caller's (`cli.py` passes `runs/`), so moving the
tuner's state elsewhere moves the lock with one argument.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

LOCK_FILE_PREFIX = ".lock-"


class RunLockError(RuntimeError):
    """Another kiln-harness-tuner run is live against the same task (two concurrent
    runs race the baseline record and the memory store)."""


class _LockBackend(Protocol):
    def try_lock(self, fd: int) -> bool:
        """Take the lock without waiting. False when another holder has it."""
        ...

    def unlock(self, fd: int) -> None: ...


class _FlockBackend:
    """macOS and Linux: a whole-file `flock`."""

    def try_lock(self, fd: int) -> bool:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def unlock(self, fd: int) -> None:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


class _MsvcrtBackend:
    """Windows: a one-byte `msvcrt.locking` region.

    Windows byte-range locks are mandatory: no other process can read a
    locked byte. The locked byte is far past the record, so a refused run
    can still read who holds the lock. Windows allows a lock past the end
    of the file, and the file does not grow."""

    OFFSET = 1 << 30

    # A type check on macOS or Linux sees no `msvcrt` names (the stub is
    # Windows-only), hence the `type: ignore`s.

    def try_lock(self, fd: int) -> bool:
        import msvcrt

        os.lseek(fd, self.OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
        return True

    def unlock(self, fd: int) -> None:
        import msvcrt

        os.lseek(fd, self.OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]


def _backend() -> _LockBackend:
    return _MsvcrtBackend() if sys.platform == "win32" else _FlockBackend()


def _lock_holder_pid(lock_path: Path) -> int | None:
    """The pid a lock file names: the JSON record this code writes, or the
    bare pid an older lock carried. Garbage reads as no holder."""
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        record = json.loads(text)
        if isinstance(record, dict) and isinstance(record.get("pid"), int):
            return record["pid"]
    except ValueError:
        pass
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _older_holder_alive(pid: int) -> bool:
    """A live pid in the record of a lock nobody holds: an older
    kiln-harness-tuner, which claimed the lock by its pid record alone. Only a
    POSIX run can be one, because no older one started on Windows. On Windows
    `os.kill(pid, 0)` is not a probe: it sends a Ctrl-C event or ends the
    process."""
    return sys.platform != "win32" and pid != os.getpid() and _pid_alive(pid)


@dataclass
class RunLock:
    """The held one-run-per-task lock: the file's path and the descriptor
    that holds the OS lock. `release` drops the lock and closes the fd; the
    file itself is never unlinked."""

    path: Path
    fd: int
    _backend: _LockBackend = field(default_factory=_backend, repr=False)

    def release(self) -> None:
        if self.fd < 0:
            return
        with contextlib.suppress(OSError):
            self._backend.unlock(self.fd)
        with contextlib.suppress(OSError):
            os.close(self.fd)
        self.fd = -1


def lock_path(lock_dir: Path, task_id: str, project_id: str | None = None) -> Path:
    """The lock file for one task. The same task id can exist in two
    projects, so the key carries the project id when it is known."""
    key = f"{project_id}-{task_id}" if project_id else task_id
    return lock_dir / f"{LOCK_FILE_PREFIX}{key}"


def acquire_run_lock(
    lock_dir: Path, task_id: str, project_id: str | None = None
) -> RunLock:
    """Take the task's lock, or raise RunLockError naming the live holder."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_path(lock_dir, task_id, project_id)
    backend = _backend()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        locked = backend.try_lock(fd)
    except BaseException:
        os.close(fd)
        raise
    if not locked:
        holder = _lock_holder_pid(path)
        os.close(fd)
        raise RunLockError(
            f"another kiln-harness-tuner run (pid {holder or '?'}) is live against task"
            f" {task_id} — one run per task.\n"
            "fix: wait for that run to finish, or stop it — the OS"
            f" releases the lock when its process ends. The lock is {path}."
        )
    holder = _lock_holder_pid(path)
    if holder is not None and _older_holder_alive(holder):
        os.close(fd)  # closing releases the lock
        raise RunLockError(
            f"another kiln-harness-tuner run (pid {holder}) is live against task"
            f" {task_id} — one run per task.\n"
            f"fix: wait for that run to finish. If pid {holder} is not a"
            f" kiln-harness-tuner run, delete {path} and retry."
        )
    record = json.dumps(
        {
            "pid": os.getpid(),
            "started": datetime.now().isoformat(timespec="seconds"),
            "project_id": project_id,
            "task_id": task_id,
        },
        ensure_ascii=False,
    )
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, record.encode("utf-8"))
    return RunLock(path, fd, backend)
