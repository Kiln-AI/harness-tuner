"""A stand-in for the Kiln studio server, for the packaging smoke test.

The tuner's start-up checks call Kiln before a run starts: they ping it, find
the project and the task, read the run configs and the evals, and check that
Kiln has a key for each provider the run calls. This server answers those
reads from one Kiln project on disk, the copy the smoke run uses. Every other
request gets HTTP 404, so the run stops at the first Kiln call it needs after
the start: the smoke test checks how far it got, not what Kiln would answer.

It answers only GET. A POST or a write gets 405. It is stdlib only, and it
never imports `kiln_harness_tuner`: the installed copy is what the smoke test
tests. Each request is kept in `FakeKiln.requests`, as `METHOD /path`.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _children(folder: Path, file_name: str) -> list[dict[str, Any]]:
    """The `file_name` model of each child folder of `folder`, by folder name."""
    if not folder.is_dir():
        return []
    return [
        _read(child / file_name)
        for child in sorted(folder.iterdir())
        if (child / file_name).is_file()
    ]


class _Project:
    """One Kiln project on disk, read the way the Kiln API serves it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.project = {
            **_read(root / "project.kiln"),
            "path": str(root / "project.kiln"),
        }
        self.id = str(self.project["id"])

    def _task_dirs(self) -> Iterator[Path]:
        tasks = self.root / "tasks"
        if tasks.is_dir():
            for task_dir in sorted(tasks.iterdir()):
                if (task_dir / "task.kiln").is_file():
                    yield task_dir

    def task_dir(self, task_id: str) -> Path | None:
        for task_dir in self._task_dirs():
            if str(_read(task_dir / "task.kiln")["id"]) == task_id:
                return task_dir
        return None

    def tasks(self) -> list[dict[str, Any]]:
        return [_read(task_dir / "task.kiln") for task_dir in self._task_dirs()]

    def eval_dir(self, task_dir: Path, eval_id: str) -> Path | None:
        for eval_dir in sorted((task_dir / "evals").glob("*")):
            eval_file = eval_dir / "eval.kiln"
            if eval_file.is_file() and str(_read(eval_file)["id"]) == eval_id:
                return eval_dir
        return None


# The reads the start-up checks make, under `/api/projects/<project>/tasks/
# <task>`. Each pattern's groups are the ids in the path.
_TASK = r"/api/projects/([^/]+)/tasks/([^/]+)"
_EVAL = _TASK + r"/evals/([^/]+)"


class FakeKiln:
    """The server, on 127.0.0.1 and a free port. Use it as a context manager:
    it serves on a thread until the block ends."""

    def __init__(self, project_root: Path) -> None:
        self.project = _Project(project_root)
        self.requests: list[str] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def __enter__(self) -> FakeKiln:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def answer(self, path: str) -> Any:
        """The body for `GET path`, or `None` for a 404."""
        project = self.project
        if path == "/ping":
            return "pong"
        if path == "/api/projects":
            return [project.project]
        if path == "/api/available_models":
            return self._available_models()
        if match := re.fullmatch(r"/api/projects/([^/]+)", path):
            return project.project if match[1] == project.id else None
        if match := re.fullmatch(r"/api/projects/([^/]+)/tasks", path):
            return project.tasks() if match[1] == project.id else None
        if match := re.fullmatch(r"/api/projects/([^/]+)/memories", path):
            # Kiln's list envelope, with no memories: a project the tuner has
            # never run on.
            empty = {"listings": [], "matched": 0, "remaining": 0}
            return empty if match[1] == project.id else None
        if match := re.fullmatch(_TASK + r"(/.*)?", path):
            if match[1] != project.id:
                return None
            task_dir = project.task_dir(match[2])
            return None if task_dir is None else self._task_answer(task_dir, path)
        return None

    def _task_answer(self, task_dir: Path, path: str) -> Any:
        rest = re.sub(_TASK, "", path, count=1)
        if rest == "":
            return _read(task_dir / "task.kiln")
        if rest == "/run_configs":
            return _children(task_dir / "run_configs", "task_run_config.kiln")
        if rest == "/specs":
            return _children(task_dir / "specs", "spec.kiln")
        if rest == "/evals":
            return _children(task_dir / "evals", "eval.kiln")
        if match := re.fullmatch(r"/evals/([^/]+)(/.*)?", rest):
            eval_dir = self.project.eval_dir(task_dir, match[1])
            if eval_dir is None:
                return None
            sub = match[2] or ""
            if sub == "":
                return _read(eval_dir / "eval.kiln")
            if sub == "/eval_configs":
                return _children(eval_dir / "configs", "eval_config.kiln")
            if config := re.fullmatch(r"/eval_config/([^/]+)", sub):
                for row in _children(eval_dir / "configs", "eval_config.kiln"):
                    if str(row["id"]) == config[1]:
                        return row
        return None

    def _available_models(self) -> list[dict[str, Any]]:
        """One provider row for each provider a run config of the project
        names: Kiln lists a provider only when it has its key."""
        providers: dict[str, set[str]] = {}
        for task_dir in self.project._task_dirs():
            for row in _children(task_dir / "run_configs", "task_run_config.kiln"):
                properties = row.get("run_config_properties") or {}
                provider = properties.get("model_provider_name")
                if isinstance(provider, str):
                    providers.setdefault(provider, set()).add(
                        str(properties.get("model_name"))
                    )
        return [
            {
                "provider_id": provider,
                "provider_name": provider,
                "models": [{"id": model, "name": model} for model in sorted(models)],
            }
            for provider, models in sorted(providers.items())
        ]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self) -> str:
                path = urlsplit(self.path).path
                with fake._lock:
                    fake.requests.append(f"{self.command} {path}")
                return path

            def do_GET(self) -> None:  # noqa: N802 — the http.server name
                body = fake.answer(self._record())
                if body is None:
                    self._send(404, {"detail": "Not Found"})
                else:
                    self._send(200, body)

            def _refuse(self) -> None:
                self._record()
                self._send(405, {"detail": "the fake Kiln answers GET only"})

            do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

            def log_message(self, *_args: object) -> None:
                """Quiet: the requests are kept in `fake.requests`."""

        return Handler
