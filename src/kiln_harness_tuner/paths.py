"""The state folder: everything a run keeps outside the package.

One folder holds every run's state, wherever the tuner is installed and
whichever folder it is started from:

```
<state>/
  runs/<run_id>/                   one run's scratch, logs and configs
  runs/.lock-<project>-<task>      the one-run-per-task lock
  runs/.quarantine/<project>-<task>/   archived probe and eval configs
  traces/<project>/<task>/<run_id>/    copied session transcripts
  reports/                         the run reports
  agents/<cli>/<version>/          the agent files a run's sessions read
```

It is `$KILN_HARNESS_TUNER_HOME`, else `~/.kiln_ai/harness_tuner/` (Kiln
keeps its own settings in `~/.kiln_ai`). It holds session transcripts and val
data, so it is created private (mode 0700).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

STATE_HOME_ENV = "KILN_HARNESS_TUNER_HOME"
DEFAULT_STATE_HOME = "~/.kiln_ai/harness_tuner"


class StateHomeError(RuntimeError):
    """The state folder cannot be used. A start-up error with a fix line."""


@dataclass(frozen=True)
class StatePaths:
    """The state folder's layout. `root` is absolute and resolved."""

    root: Path

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def traces(self) -> Path:
        return self.root / "traces"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def policies(self) -> Path:
        """The model policies the guided start's picker writes. Made when
        the first one is written."""
        return self.root / "policies"

    def quarantine(self, project_id: str, task_id: str) -> Path:
        """Where a task's probe and eval configs are archived. Under `runs/`,
        which the read fence denies, so no agent reads it."""
        return self.runs / ".quarantine" / f"{project_id}-{task_id}"


def state_home(
    environ: Mapping[str, str] | None = None, cwd: Path | None = None
) -> Path:
    """The state folder's path: `$KILN_HARNESS_TUNER_HOME` (an empty value
    counts as unset), else `DEFAULT_STATE_HOME`. `~` is expanded, and a
    relative path is joined to `cwd` (default: the current folder). Creates
    nothing."""
    env = os.environ if environ is None else environ
    path = Path(env.get(STATE_HOME_ENV) or DEFAULT_STATE_HOME).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    return path.resolve()


def prepare_state(root: Path) -> StatePaths:
    """Create the state folder and its layout, and prove it is writable.

    A new folder gets mode 0700; its missing parents get the default mode,
    and an existing folder's mode is left alone."""
    state = StatePaths(root)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in (state.runs, state.traces, state.reports, state.agents):
            folder.mkdir(exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write-check-", dir=root)
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        raise StateHomeError(
            f"the state folder {root} cannot be used ({exc}).\n"
            f"fix: set {STATE_HOME_ENV} to a writable folder."
        ) from exc
    return state


def check_no_overlap(state: StatePaths, project_root: Path) -> None:
    """Refuse a state folder and a Kiln project where either one is inside
    the other. The fences would contradict each other: the project is denied
    to the agents whole, and their scratch, in the state folder, must be
    writable."""
    root, project = state.root.resolve(), project_root.resolve()
    if root.is_relative_to(project) or project.is_relative_to(root):
        raise StateHomeError(
            f"the state folder {root} and the Kiln project {project} overlap:"
            " the agents may not read the project, and they must write in the"
            " state folder.\n"
            f"fix: set {STATE_HOME_ENV} to a folder outside the Kiln project,"
            " or move the project out of the state folder."
        )
