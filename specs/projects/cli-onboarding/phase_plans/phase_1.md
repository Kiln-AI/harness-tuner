---
status: complete
---

# Phase 1: Wiring, prompts and the Kiln check

## Overview

This phase adds the `onboarding/` subpackage and wires it into `cli.main`. After it, every start first checks that Kiln answers and has the APIs the tuner needs (architecture §6). An interactive user gets the shared `[r]etry  [o]pen kiln.tech  e[x]it:` prompt (§3). A non-interactive caller gets the message on stderr and exit 1. The three ID flags stay required: project and task selection is phase 2. The shared prompt, the exceptions, the runner and the Kiln UI link module are the base every later check uses.

## Steps

1. **`src/kiln_harness_tuner/onboarding/errors.py`.** Two exceptions (§4):

   ```
   class OnboardingError(Exception): ...          # complete message; main prints "<prog>: <message>", exit 1
   class OnboardingStopped(Exception):
       def __init__(self, exit_code: int): ...    # self.exit_code: 1 = x or EOF, 130 = Ctrl-C
   ```

2. **`onboarding/terminal.py`** (§3):
   - `Terminal` protocol: `interactive: bool`, `say`, `warn`, `ask`, `open_url`.
   - `StdTerminal(prog)`:
     - `interactive` is `sys.stdin.isatty() and sys.stdout.isatty()`, computed once. A missing stream (`None`) counts as not a terminal.
     - `say` prints to stdout. `warn` prints `"<prog>: <text>"` to stderr.
     - `ask` calls `input(prompt)`. `EOFError` prints a newline, then raises `OnboardingStopped(1)`. `KeyboardInterrupt` prints a newline, then raises `OnboardingStopped(130)`.
     - `open_url` calls `webbrowser.open(url)`, then `say(url)`.
   - `@dataclass(frozen=True) class Problem: message: str; open_url: str | None = None; open_label: str | None = None`.
   - `class Resolution(Enum): OK, PICK_TASK`.
   - `resolve_problem(check, terminal, *, offer_pick_task=False) -> Resolution`:
     - Not interactive: raise `OnboardingError(problem.message)`.
     - Interactive: `say(message)`, then ask with the options that apply. `""`/`r` re-checks, `o` opens and asks again, `t` returns `PICK_TASK`, `x` raises `OnboardingStopped(1)`, and anything else asks again.

3. **`onboarding/kiln_urls.py`.** One function per Kiln web UI link (§16):
   - `create_project(host)`: `<host>/settings/create_project`.
   - `create_task(host, project_id)`: `<host>/settings/create_task/<project_id>`.
   - `run_configs(host, project_id, task_id)`: `<host>/optimize/<project_id>/<task_id>`.
   - `evals(host, project_id, task_id)`: `<host>/specs/<project_id>/<task_id>`.
   - `providers(host)`: `<host>/settings/providers`.

   Nothing calls them yet. Phases 2 and 3 use them.

4. **`onboarding/kiln_check.py`** (§6):
   - `KILN_DOWNLOAD_URL`, `PING_TIMEOUT_SECS = 5.0`, `OPENAPI_TIMEOUT_SECS = 10.0`.
   - `RequiredApi` and `REQUIRED_APIS`: the memory API, the eval-job API and run-config provenance.
   - `KilnCheck(problem, warning)`.
   - `missing_apis(openapi) -> list[str]`: pure. The labels come back in `REQUIRED_APIS` order. A missing or non-dict level counts as absent.
   - `async check_kiln(kiln, host, *, prog) -> KilnCheck`:
     - `GET /ping`, no retries, 5 s. A transport error gives the not-running problem. An HTTP error gives the not-Kiln problem with `HTTP <status>`. A body other than `"pong"` gives the not-Kiln problem with "an unexpected answer".
     - `GET /openapi.json`, no retries, 10 s. A `KilnError` or a non-dict body gives the warning only. Missing APIs give the too-old problem, which quotes `info.version` unless it is missing, empty or `unknown`.
     - Every problem opens `KILN_DOWNLOAD_URL`, labelled `kiln.tech`.
   - `prog` is a parameter (the architecture's signature has none), because the too-old message names the command.

5. **`onboarding/flow.py`** (§1, §5):
   - `KilnRunner`: a `Protocol` whose `__call__(fn: Callable[[KilnApi], Awaitable[T]]) -> T`. This is the architecture's alias, written so that `T` binds per call.
   - `kiln_runner(host) -> KilnRunner`: runs `fn` on a fresh `KilnClient(host)` inside its own `asyncio.run`, and closes the client.
   - `prepare_start(args, *, prog, terminal=None, runner=None) -> argparse.Namespace`:
     - Defaults are `StdTerminal(prog)` and `kiln_runner(host)`, where `host = args.kiln_host.rstrip("/")`.
     - It runs the Kiln check through `resolve_problem` and prints the check's warning through `terminal.warn`.
     - The body is wrapped in `except KeyboardInterrupt: raise OnboardingStopped(130)`.
     - It returns `args` unchanged in this phase.
     - `argv`, `home`, `policy_loader` and `parser_long_options` arrive with the phases that use them (2, 3 and 4).

6. **`onboarding/__init__.py`.** Re-exports `prepare_start`, `OnboardingError` and `OnboardingStopped`.

7. **`cli.py`** (§12):
   - `PROG = "kiln-harness-tuner"`, and `build_parser` passes `prog=PROG`.
   - `main`:
     - Inside the existing `try`, it calls `args = prepare_start(args, prog=PROG)` before `asyncio.run(...)`. The call goes through the module global, so tests can monkeypatch `kiln_harness_tuner.cli.prepare_start`.
     - `except OnboardingStopped` prints `"<PROG>: stopped."` to stderr and returns `exit_code`.
     - `OnboardingError` joins the existing except tuple.
     - `main`'s own `kiln-harness-tuner:` prefixes use `PROG`.
   - The three ID flags stay `required=True`.

8. **`tests/onboarding_fakes.py`** (§15):
   - `ScriptedTerminal(inputs, *, interactive=True)`:
     - It records `out`, `err`, `prompts` and `opened`.
     - `ask` pops the next input. An input that is an exception is raised. When the inputs run out, it raises `OnboardingStopped(1)`, like EOF.
   - `RouteKiln(routes)`: an async `call` that looks up `(method, path)`.
     - It returns the value, raises it if it is an exception, or calls it with `(query_params, body)` if it is callable.
     - It records every call as a `KilnCall`: method, path, query, body, timeout and retries.
     - An unrouted call raises `KilnAPIError(…, 404, …)`, as Kiln does.
   - `responses(*values)`: a callable route that answers with each value in turn and then repeats the last one.
   - `runner_for(kiln)`: a `KilnRunner` that runs `asyncio.run(fn(kiln))`.

9. **`tests/test_cli.py`:**
   - An autouse fixture patches `cli.prepare_start` to return `args` unchanged, so the existing tests stay offline.
   - New tests for the wiring (below).

## Tests

- `tests/test_onboarding_terminal.py`:
  - `test_resolve_problem_returns_ok_when_the_check_passes`: no output and no prompt.
  - `test_retry_runs_the_check_again_until_it_passes`: `r`, then the check passes. The message was printed before each prompt.
  - `test_enter_is_retry`: an empty answer re-checks.
  - `test_open_opens_the_url_and_asks_again_without_rechecking`: `o`, then `r`. The URL is opened once, and the check ran twice, not three times.
  - `test_the_prompt_shows_only_the_options_that_apply`: no `[o]` without `open_url`, no `[t]` unless offered, and the label in `[o]pen <label>`.
  - `test_pick_task_only_when_offered`: `t` returns `PICK_TASK` when offered. When it is not offered, `t` asks again.
  - `test_unknown_answers_ask_again`: `zz`, then ` R ` (normalised) re-checks.
  - `test_exit_and_eof_stop_with_1`: `x`, and the inputs running out, both raise `OnboardingStopped(1)`.
  - `test_not_interactive_raises_the_message_without_asking`: `OnboardingError` carries the message, and no prompt was shown.
  - `test_std_terminal_*`:
    - `interactive` only when both streams are TTYs, and not when a stream is missing.
    - `say` goes to stdout, `warn` to stderr with the prefix.
    - `ask` returns the line. EOF gives a newline and `OnboardingStopped(1)`. Ctrl-C gives a newline and `OnboardingStopped(130)`.
    - `open_url` calls `webbrowser.open` and prints the URL.
- `tests/test_onboarding_kiln_check.py`:
  - `test_kiln_not_running_interactive_retry_succeeds`: through `prepare_start`. Transport error, `r`, then pong. The message names the host, `kiln.tech` and `--kiln-host`, and `args` comes back.
  - `test_kiln_not_running_interactive_exit_stops_with_1`.
  - `test_kiln_not_running_not_interactive_fails_without_reading_input`: `OnboardingError` with the message, and no prompt.
  - `test_ping_uses_one_short_try`: `timeout_secs=5`, `retries=False`.
  - `test_something_else_on_the_port`: a 404 names `HTTP 404`. A non-`pong` body names "an unexpected answer".
  - `test_missing_apis_names_each_absent_api` (parametrised over the three), `test_missing_apis_all_present`, and `test_missing_apis_tolerates_missing_levels`.
  - `test_too_old_message_quotes_the_version` and `test_too_old_message_leaves_out_unknown_version` (`unknown`, empty, missing).
  - `test_open_offers_kiln_tech`: every problem opens `https://kiln.tech`.
  - `test_unreadable_openapi_warns_and_goes_on`: a 500, a non-JSON body and a transport error each give one warning line and no problem. Through `prepare_start`, the warning reaches `terminal.err` once.
  - `test_trailing_slash_on_the_host_is_dropped`: `http://h/` reads as `http://h` in the message.
  - `test_ctrl_c_during_a_kiln_read_stops_with_130`: the runner raises `KeyboardInterrupt`, and `prepare_start` raises `OnboardingStopped(130)`.
  - `test_kiln_runner_uses_a_fresh_client_on_the_host`: `respx` mocks `GET <host>/ping`, and the runner returns `fn`'s result.
- `tests/test_onboarding_kiln_urls.py`:
  - `test_each_link_names_its_kiln_page`: the five routes.
- `tests/test_cli.py`:
  - `test_the_parser_is_named_by_prog`: `build_parser().prog == PROG`.
  - `test_main_runs_prepare_start_before_the_run`: `_run` receives the namespace that `prepare_start` returned, and `prepare_start` got `prog=PROG`.
  - `test_main_prints_an_onboarding_error_and_exits_1`: the message is on stderr with the prefix.
  - `test_main_returns_the_stop_code` (parametrised 1 and 130): `kiln-harness-tuner: stopped.` is on stderr, and `_run` is never reached.
