---
status: complete
---

# Implementation Plan: New TUI

Each phase leaves `uv run pytest`, `uv run ruff check` and `uv run ruff format --check` green, and the plain path working. The app is not reachable from `harness-tuner` until phase 5.

## Phases

- [ ] Phase 1: Loop data additions. C1a, C1b, C2, C3 (with the per-item cells), C4, C5, C6 and C7 (architecture section 9; schemas in `research/codebase/data_contract_audit.md` section 2). Tests for each event; `PlainUi` output unchanged.
- [ ] Phase 2: CLI seams, with no behavior change. `prepare_start(report=)` and `StartReport`; `_run(display=)` and `_run_with_signals(display=, on_started=)`; `main` split into `display_mode`, `_plain_body` and `_finish`; `plain_exit_line` moved out of `PlainUi`; `aligned_rows`, `PICK_HINT` and `graphical_browser_expected` moved out of `console.py` (architecture sections 2, 4.3, 5.1).
- [ ] Phase 3: The run model, pure Python. `tui/model/`: `Stamped`, `UiTracker`, the state dataclasses, the fold, results tiers, cards and verdict words, timeline and wave estimate, log lines, links, `RcNames` (architecture sections 7, 8). Model tests on recorded event sequences.
- [ ] Phase 4: The app shell and the terminal. Add `textual` and raise `rich`. `tui/theme.py` with the OSC 11 query, `terminal_io.py` (TTYDriver, fd redirect, stray-output file), `opener.py`, `handles.py`, `TunerApp` with signals, bindings and the drain, `StopDialog`, `closing.py`, `entry.run_app` for all three modes with placeholder screens. Entry tests with a fake `_run`; pty tests for SIGTERM, SIGHUP, stray output and the closing block.
- [ ] Phase 5: The start-up screen. `AppTerminal`, the cancellable runner, `StartupScreen` and its widgets (architecture section 4). Port the `RichTerminal` behavior tests to pilot tests. Wire `main` to `run_app`. Remove `RichTerminal`, questionary and their tests.
- [ ] Phase 6: The run screen. `TuiSink`, the sections and widgets from `ui_preview/`, the five modes and Done, the name lookup, links and keys (architecture sections 5, 7.3, 8; ui_design). Pilot tests per mode. Remove `LiveUi` and its renderers and tests.
- [ ] Phase 7: Docs and packaging. Update `docs/install.md`, `docs/your-own-task.md`, `docs/architecture.md` and the other mentions (`research/codebase/startup_bridge.md` 5.6). The wheel smoke test checks `tuner.tcss` and the TUI import. A final pass over the screenshots with the real app.
