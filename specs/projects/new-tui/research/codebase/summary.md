# Codebase research: what the TUI has to work with

Two read-only inventories of `main` at f33d877 (CLI onboarding merged), written to ground the new TUI's specs. Cite them by file:line; re-check before relying on a line number, since code moves.

- [cli_and_startup.md](cli_and_startup.md): every CLI flag, how the display mode is chosen today, the start-up flow step by step with its exact strings, the hand-off to the run, exit and interruption, dependencies, and what matters when replacing the terminal UI.
- [runtime_data.md](runtime_data.md): how events reach the display, every event kind the loop emits, `RunStatus` and friends, a per-display-need verdict (emitted, computed but not emitted, derivable, or unavailable), the run files on disk, and threading caveats.

Headline findings:

- The command is `harness-tuner`. 27 flags, no positional arguments, no subcommands. Display is chosen by three separate tests (guided start: stdin and stdout are TTYs and no `--no-interaction`; run display: `--no-progress` or rich's `is_terminal`; error screen: stderr is a TTY).
- The start-up flow is synchronous behind the `Terminal` protocol (`onboarding/terminal.py`). It can't run inside a running Textual loop as is; a worker thread that blocks on the UI is the natural seam.
- Today's scrollback is part of the product: the step lines, the summary, the rerun command and the frozen waves. A full-screen app erases it.
- Events reach the display through `FanoutLog` on the loop's thread, with no guard. A display exception would reach the orchestrator.
- The loop blocks its own thread at times (`reconcile()`, an `opencode export` of up to 60 s), so a UI sharing that loop would freeze.
- Already emitted: objective, spend by lane, budget and clock, wrap-up flag, phases, item states and spend, verdicts and stop reasons, probe answers, screening results, gate decisions, health signals, exit reason, report path.
- Gaps, each a few lines to close: per-eval train results per experiment, val tables per gate, structured chunk counts, per-fill gate progress. `eval_progress` is folded by the tracker but never emitted.
- No code builds Kiln Compare or Lineage URLs. The known web routes are in `onboarding/kiln_urls.py`.
