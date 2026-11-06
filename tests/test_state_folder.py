"""The state folder (`paths.py`): where it is, how it is made, and the one
layout it refuses."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kiln_harness_tuner.paths import (
    STATE_HOME_ENV,
    StateHomeError,
    StatePaths,
    check_no_overlap,
    prepare_state,
    state_home,
)


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


class TestWhereItIs:
    def test_unset_is_the_kiln_folder_in_home(self, home):
        assert state_home({}) == home.resolve() / ".kiln_ai" / "harness_tuner"

    def test_empty_counts_as_unset(self, home):
        assert state_home({STATE_HOME_ENV: ""}) == state_home({})

    def test_the_tilde_is_expanded(self, home):
        assert state_home({STATE_HOME_ENV: "~/tuner"}) == home.resolve() / "tuner"

    def test_a_relative_path_is_joined_to_the_current_folder(self, tmp_path):
        cwd = tmp_path / "here"
        cwd.mkdir()
        assert state_home({STATE_HOME_ENV: "state"}, cwd=cwd) == cwd / "state"
        assert state_home({STATE_HOME_ENV: "../up"}, cwd=cwd) == tmp_path / "up"

    def test_the_path_is_resolved_and_nothing_is_created(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        found = state_home({STATE_HOME_ENV: str(tmp_path / "link" / "state")})
        assert found == real / "state"
        assert not found.exists()

    def test_the_environment_is_read_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv(STATE_HOME_ENV, str(tmp_path / "env-state"))
        assert state_home() == tmp_path / "env-state"


def test_the_layout():
    state = StatePaths(Path("/s"))
    assert (state.runs, state.traces, state.reports, state.agents) == (
        Path("/s/runs"),
        Path("/s/traces"),
        Path("/s/reports"),
        Path("/s/agents"),
    )
    # Today's quarantine naming, under the runs folder the read fence denies.
    assert state.quarantine("p1", "t1") == Path("/s/runs/.quarantine/p1-t1")


class TestPrepare:
    def test_a_new_folder_is_private_with_its_layout(self, tmp_path):
        root = tmp_path / "missing" / "parent" / "state"
        state = prepare_state(root)
        assert state.root == root
        assert stat.S_IMODE(root.stat().st_mode) & 0o077 == 0
        for folder in (state.runs, state.traces, state.reports, state.agents):
            assert folder.is_dir()
        # The write check leaves nothing behind.
        assert sorted(p.name for p in root.iterdir()) == [
            "agents",
            "reports",
            "runs",
            "traces",
        ]

    def test_an_existing_folders_mode_is_left_alone(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        root.chmod(0o755)
        prepare_state(root)
        assert stat.S_IMODE(root.stat().st_mode) == 0o755

    def test_a_folder_that_cannot_be_written_is_refused(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root writes through any mode")
        locked = tmp_path / "locked"
        locked.mkdir(mode=0o500)
        try:
            with pytest.raises(StateHomeError) as exc:
                prepare_state(locked / "state")
        finally:
            locked.chmod(0o700)
        assert str(locked / "state") in str(exc.value)
        assert f"fix: set {STATE_HOME_ENV} to a writable folder." in str(exc.value)

    def test_a_file_in_the_way_is_refused(self, tmp_path):
        root = tmp_path / "state"
        root.write_text("not a folder")
        with pytest.raises(StateHomeError) as exc:
            prepare_state(root)
        message = str(exc.value)
        assert message.startswith(f"the state folder {root} cannot be used (")
        assert "\nfix: " in message


class TestOverlap:
    @pytest.mark.parametrize(
        ("state", "project"),
        [
            ("work/state", "work/state/Forum Moderation"),
            ("work/Forum Moderation/state", "work/Forum Moderation"),
            ("work/same", "work/same"),
        ],
    )
    def test_either_inside_the_other_is_refused(self, tmp_path, state, project):
        with pytest.raises(StateHomeError) as exc:
            check_no_overlap(StatePaths(tmp_path / state), tmp_path / project)
        assert f"fix: set {STATE_HOME_ENV} to a folder outside" in str(exc.value)

    def test_siblings_pass(self, tmp_path):
        # A shared name prefix is not containment.
        check_no_overlap(StatePaths(tmp_path / "state"), tmp_path / "state-project")
