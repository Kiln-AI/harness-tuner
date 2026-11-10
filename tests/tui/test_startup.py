"""The start-up screen and its prompts (architecture 4.4, ui_design section 8),
pilot at 210×62. Each test runs a start on the start-up thread behind the
real `AppTerminal`, presses keys, and checks what the start got back. These
are the behaviors the questionary prompts had (functional spec section 5)."""

import asyncio
import threading
from contextlib import asynccontextmanager

import pytest
from app_fakes import SIZE, Asking, answered, prompted, until
from textual.content import Content
from textual.widgets import Input, OptionList, Static

from kiln_harness_tuner.onboarding import demo
from kiln_harness_tuner.onboarding.choose import TaskRow, _step
from kiln_harness_tuner.onboarding.errors import OnboardingStopped
from kiln_harness_tuner.onboarding.flow import WELCOME_DESCRIPTION
from kiln_harness_tuner.onboarding.summary import RERUN_HEADING, SUMMARY_HEADING
from kiln_harness_tuner.onboarding.terminal import (
    ENTER,
    PICK_HINT,
    Action,
    ActionKind,
    Option,
    aligned_rows,
)
from kiln_harness_tuner.tui import opener
from kiln_harness_tuner.tui.bridge import CancellableRunner
from kiln_harness_tuner.tui.screens.prompts import ActionMenu, Picker, TextAnswer
from kiln_harness_tuner.tui.screens.startup import task_name
from kiln_harness_tuner.tui.widgets import BusyLine, StepLineView, TitleBar

PROJECTS = [
    Option("p1", "Forum Moderation", ("424242975950", "~/Kiln Projects/Forum")),
    Option("p2", "Support Triage", ("118230554411", "~/Kiln Projects/Support")),
    Option("p3", "Spam", ("731954460212", "")),
]
CLIS = [
    Option("claude", "Claude Code"),
    Option("codex", "Codex", disabled="not installed"),
    Option("opencode", "OpenCode"),
]
ACTIONS = [
    Action("r", "Check again", ActionKind.RETRY),
    Action("d", "Open the download page", ActionKind.OPEN, url="https://k/dl"),
    Action("x", "Exit", ActionKind.EXIT),
]
ADVANCED = [
    Action(ENTER, "Keep these", ActionKind.KEEP),
    Action("c", "Customize", ActionKind.CUSTOMIZE),
]


def pick(options, **kwargs):
    return lambda terminal: terminal.pick("Which?", options, **kwargs)


def act(actions, question="What next?"):
    return lambda terminal: terminal.act(question, actions)


def text(**kwargs):
    kwargs.setdefault("default", "50")
    return lambda terminal: terminal.text("Budget?", **kwargs)


def rendered(widget) -> str:
    return str(widget.render())


def plain(markup: str | None) -> str:
    """A border title's text: Textual hands it back as markup."""
    return Content.from_markup(markup or "").plain


def lines(app) -> list[str]:
    """The text of each line in the transcript, in order: not its cards and
    its prompts."""
    column = app.screen.query_one("#column")
    return [rendered(part) for part in column.children if isinstance(part, Static)]


@asynccontextmanager
async def asking(ask, *, size=SIZE, **kwargs):
    """The app over `ask`, at its first prompt."""
    harness = Asking(ask, **kwargs)
    async with harness.app.run_test(size=size) as pilot:
        await prompted(pilot)
        yield harness, pilot
        if not harness.ended:
            await pilot.press("ctrl+c")
        await until(pilot, lambda: harness.app.return_code is not None)


async def still_asking(harness, pilot, *keys) -> bool:
    """Whether the prompt is still open after `keys`."""
    await pilot.press(*keys)
    await pilot.pause(0.1)
    return not harness.ended


# ---- pick ----


async def test_pick_by_arrow_keys():
    assert await answered(pick(PROJECTS), "down", "down", "up", "enter") == "p2"


async def test_pick_by_filter_text_on_a_column():
    assert await answered(pick(PROJECTS), *"1182", "enter") == "p2"
    # The filter ignores case.
    assert await answered(pick(PROJECTS), *"spa", "enter") == "p3"


async def test_pick_skips_a_disabled_option():
    assert await answered(pick(CLIS), "down", "enter") == "opencode"
    # Up from the first row wraps to the last, then skips Codex.
    assert await answered(pick(CLIS), "up", "up", "enter") == "claude"


async def test_enter_never_picks_a_disabled_option_the_filter_leaves():
    async with asking(pick(CLIS)) as (harness, pilot):
        assert await still_asking(harness, pilot, *"codex", "enter")


async def test_a_filter_puts_the_cursor_on_its_first_enabled_row():
    disabled_first = [CLIS[1], CLIS[2], CLIS[0]]  # Codex, OpenCode, Claude Code
    # `co` matches all three; the cursor skips Codex.
    assert await answered(pick(disabled_first), *"co", "enter") == "opencode"
    # Backspace filters again, and the cursor skips it again.
    typed = [*"cox", "backspace", "enter"]
    assert await answered(pick(disabled_first), *typed) == "opencode"


async def test_the_arrows_skip_disabled_rows_of_the_filtered_list():
    options = [
        Option("zed", "Zed"),
        Option("copilot", "Copilot"),
        Option("codex", "Codex", disabled="not installed"),
        Option("opencode", "OpenCode"),
    ]
    # `co` leaves Copilot, Codex and OpenCode, with the cursor on Copilot.
    assert await answered(pick(options), *"co", "down", "enter") == "opencode"
    assert await answered(pick(options), *"co", "up", "enter") == "opencode"


async def test_enter_does_nothing_while_the_filter_matches_nothing():
    async with asking(pick(PROJECTS)) as (harness, pilot):
        assert await still_asking(harness, pilot, *"zzz", "enter")
        assert pilot.app.screen.query_one(Picker).query_one(OptionList).options == []
        await pilot.press("backspace", "backspace", "backspace", "down", "enter")
        await until(pilot, lambda: harness.ended)
    assert harness.start.outcome.value == "p2"


async def test_pick_starts_on_its_default():
    assert await answered(pick(PROJECTS, default="p2"), "enter") == "p2"
    assert await answered(pick(PROJECTS, default="p2"), "down", "enter") == "p3"


async def test_pick_starts_on_the_first_enabled_row_for_a_disabled_default():
    assert await answered(pick(CLIS, default="codex"), "enter") == "claude"
    assert await answered(pick(CLIS, default="nothing"), "enter") == "claude"


async def test_a_picker_shows_aligned_rows_reasons_and_its_hint():
    async with asking(pick(CLIS)) as (_harness, pilot):
        picker = pilot.app.screen.query_one(Picker)
        rows = [str(row.prompt) for row in picker.query_one(OptionList).options]
        assert rows == [
            " Claude Code ",
            f" {aligned_rows(CLIS)[1]}         not installed ",
            " OpenCode ",
        ]
        assert rendered(picker.query_one(".question")) == "Which?"
        assert rendered(picker.query_one(".hint")) == PICK_HINT
        # The filter has the focus, so typing filters.
        assert isinstance(pilot.app.focused, Input)
    hinted = pick(PROJECTS, hint="One objective per run.")
    async with asking(hinted) as (_harness, pilot):
        hint = pilot.app.screen.query_one(Picker).query_one(".hint")
        assert rendered(hint) == "One objective per run."


async def test_the_highlighted_row_shows_its_description():
    options = [
        Option("quality", "Quality", description="Raise the eval scores."),
        Option("speed", "Speed"),
        Option("cost", "Cost", description="Lower the cost per request."),
    ]
    async with asking(pick(options)) as (_harness, pilot):
        picker = pilot.app.screen.query_one(Picker)

        def description():
            return rendered(picker.query_one(".description"))

        assert description() == "Raise the eval scores."
        await pilot.press("down")
        assert description() == ""
        await pilot.press("down")
        assert description() == "Lower the cost per request."


async def test_a_prompt_the_screen_cannot_show_fails_instead_of_waiting():
    harness = Asking(pick([]))
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: harness.ended)
    assert isinstance(harness.start.outcome.error, ValueError)


# ---- act ----


async def test_act_by_its_key_with_no_enter():
    # Nothing follows the `d`: the key acts at once.
    assert await answered(act(ACTIONS), "d") is ACTIONS[1]


async def test_act_by_arrows_and_enter():
    assert await answered(act(ACTIONS), "down", "down", "enter") is ACTIONS[2]


async def test_act_ignores_a_key_no_action_has():
    assert await answered(act(ACTIONS), "q", "enter") is ACTIONS[0]


async def test_an_enter_action_is_picked_by_enter_and_the_others_by_their_keys():
    assert await answered(act(ADVANCED), "enter") is ADVANCED[0]
    assert await answered(act(ADVANCED), "c") is ADVANCED[1]
    assert await answered(act(ADVANCED), "down", "enter") is ADVANCED[1]


async def test_act_draws_each_key_before_its_label():
    for actions, rows in [
        (ADVANCED, [" Enter) Keep these ", " c) Customize "]),
        (ACTIONS, [" r) Check again ", " d) Open the download page ", " x) Exit "]),
    ]:
        async with asking(act(actions, "Advanced options")) as (_harness, pilot):
            menu = pilot.app.screen.query_one(ActionMenu)
            assert rendered(menu.query_one(".question")) == "Advanced options"
            assert [str(row.prompt) for row in menu.query_one(OptionList).options] == (
                rows
            )


# ---- text ----


async def test_text_enter_accepts_the_default():
    assert await answered(text(hint="USD"), "enter") == "50"


async def test_text_typed_after_the_default():
    typed = ["backspace", "backspace", *"12.5", "enter"]
    assert await answered(text(), *typed) == "12.5"


def digits(answer: str) -> str | None:
    return None if answer.isdigit() else "Enter a number"


async def test_text_refuses_an_answer_and_keeps_what_was_typed():
    async with asking(text(validate=digits, hint="USD")) as (harness, pilot):
        answer = pilot.app.screen.query_one(TextAnswer)
        assert rendered(answer.query_one(".hint")) == "USD"
        assert await still_asking(harness, pilot, "x", "enter")
        assert answer.query_one(Input).value == "50x"
        assert rendered(answer.query_one(".error")) == "Enter a number"
        assert answer.query_one(Input).has_class("-invalid")
        # An edit clears the message; the next Enter answers.
        await pilot.press("backspace")
        assert rendered(answer.query_one(".error")) == ""
        await pilot.press("enter")
        await until(pilot, lambda: harness.ended)
    assert harness.start.outcome.value == "50"


async def test_a_validator_that_raises_fails_the_prompt_with_its_error():
    def broken(_answer):
        raise ValueError("the policy file can't be read")

    error = await answered(text(validate=broken), "enter")
    assert isinstance(error, ValueError)


# ---- stopping ----

PROMPTS = {
    "pick": pick(PROJECTS),
    "act": act(ACTIONS),
    "text": text(),
}


@pytest.mark.parametrize("prompt", PROMPTS)
@pytest.mark.parametrize("key,code", [("ctrl+c", 130), ("ctrl+d", 1)])
async def test_ctrl_c_and_ctrl_d_stop_every_prompt(prompt, key, code):
    harness = Asking(PROMPTS[prompt])
    async with harness.app.run_test(size=SIZE) as pilot:
        await prompted(pilot)
        footer = {
            key: active.binding.description
            for key, active in pilot.app.screen.active_bindings.items()
            if active.binding.show and active.enabled
        }
        assert list(footer.items()) == [("ctrl+c", "Stop"), ("ctrl+d", "Exit")]
        await pilot.press(key)
        await until(pilot, lambda: harness.app.return_code is not None)
    # The prompt raised the stop, so the start ended before the app closed.
    error = harness.start.outcome.error
    assert isinstance(error, OnboardingStopped) and error.exit_code == code
    assert harness.start.stopped.exit_code == code


class StuckKiln:
    """A Kiln whose every call waits until it is cancelled."""

    def __init__(self):
        self.calling = threading.Event()
        self.cancelled = False
        self.closed = False

    async def call(self, *_args, **_kwargs):
        self.calling.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def connect(self):
        kiln = self

        @asynccontextmanager
        async def connected():
            try:
                yield kiln
            finally:
                kiln.closed = True

        return connected()


async def test_ctrl_c_during_a_busy_check_cancels_its_kiln_read():
    kiln = StuckKiln()
    runner = CancellableRunner(kiln.connect)

    def check(terminal):
        with terminal.busy("Checking Kiln at http://localhost:8757…"):
            return runner(lambda api: api.call("GET", "/ping"))

    # A long grace: the app closes because the start ended, not on a timer.
    harness = Asking(check, runner=runner, stop_grace=30)
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, kiln.calling.is_set)
        await until(pilot, lambda: pilot.app.screen.query(BusyLine))
        busy = pilot.app.screen.query_one(BusyLine)
        assert rendered(busy)[2:] == "Checking Kiln at http://localhost:8757…"
        await pilot.press("ctrl+c")
        await until(pilot, lambda: harness.app.return_code is not None, timeout=5)
    assert isinstance(harness.start.outcome.error, KeyboardInterrupt)
    assert kiln.cancelled and kiln.closed
    assert harness.start.stopped.exit_code == 130


def stubborn(release: threading.Event):
    """A start that does not unwind on a stop, as a file copy does not."""

    def start(terminal):
        terminal.step("ok", "Kiln", "1.2.0")
        release.wait(10)

    return start


async def test_a_start_that_does_not_unwind_closes_after_the_grace_period():
    release = threading.Event()
    harness = Asking(stubborn(release), stop_grace=0.2)
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: pilot.app.screen.query(StepLineView))
        await pilot.press("ctrl+c")
        await until(pilot, lambda: harness.app.return_code is not None, timeout=2)
    assert harness.start.stopped.exit_code == 130
    release.set()


async def test_a_second_ctrl_c_closes_at_once():
    release = threading.Event()
    harness = Asking(stubborn(release), stop_grace=30)
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: pilot.app.screen.query(StepLineView))
        await pilot.press("ctrl+c")
        await pilot.pause(0.1)
        assert harness.app.return_code is None
        await pilot.press("ctrl+c")
        await until(pilot, lambda: harness.app.return_code is not None, timeout=2)
    release.set()


def copying(release: threading.Event):
    """A start in the demo copy, which a stop can't cut short."""

    def start(terminal):
        with terminal.busy(demo.COPYING):
            release.wait(10)

    return start


async def test_a_stop_during_the_demo_copy_waits_for_the_start_to_end():
    release = threading.Event()
    harness = Asking(copying(release), stop_grace=0.1)
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: harness.terminal.copying_demo)
        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        # No grace period: the copy, and its cleanup, finish first.
        assert harness.app.return_code is None
        release.set()
        await until(pilot, lambda: harness.app.return_code is not None, timeout=2)
    assert harness.ended
    assert harness.start.stopped.exit_code == 130


async def test_a_second_stop_during_the_demo_copy_closes_at_once():
    release = threading.Event()
    harness = Asking(copying(release), stop_grace=30)
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: harness.terminal.copying_demo)
        await pilot.press("ctrl+c")
        await pilot.pause(0.1)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: harness.app.return_code is not None, timeout=2)
    assert not harness.ended
    release.set()


# ---- what the transcript shows ----


def card_text(app, selector: str) -> tuple[str, str, str]:
    """The one card `selector` names: its title, its subtitle and its text."""
    (card,) = app.screen.query(selector)
    return (
        plain(card.border_title),
        plain(card.border_subtitle),
        rendered(card.query_one(Static)),
    )


async def show(lines_of, ask):
    """The transcript's lines once `ask` has shown its lines and asks."""

    def start(terminal):
        ask(terminal)
        return terminal.act("What next?", ACTIONS)

    async with asking(start) as (_harness, pilot):
        return lines_of(pilot.app)


async def test_a_step_line_is_a_glyph_a_label_and_the_text():
    def steps(terminal):
        terminal.step("ok", "Kiln", "1.2.0 at http://localhost:8757")
        terminal.step("fail", "API keys", "missing")
        terminal.step("warn", "Ready", "maybe")
        terminal.warn("Couldn't read Kiln's API list, so its version wasn't checked.")

    assert await show(lines, steps) == [
        "✓ Kiln      1.2.0 at http://localhost:8757",
        "✗ API keys  missing",
        "! Ready     maybe",
        "! Couldn't read Kiln's API list, so its version wasn't checked.",
    ]


async def test_names_show_as_typed():
    def typed(terminal):
        terminal.step("ok", "Project", "Moderation [/old] [b]x[/b] :ok:")
        terminal.say("harness-tuner --guidance 'Keep :ok: posts [red]'")

    assert await show(lines, typed) == [
        "✓ Project   Moderation [/old] [b]x[/b] :ok:",
        "harness-tuner --guidance 'Keep :ok: posts [red]'",
    ]


async def test_the_welcome_card_holds_the_title_the_version_and_the_description():
    def welcome(terminal):
        terminal.welcome("Kiln Harness Tuner", "0.1.0", WELCOME_DESCRIPTION)

    assert await show(lambda app: card_text(app, "#welcome"), welcome) == (
        "Kiln Harness Tuner",
        "v0.1.0",
        WELCOME_DESCRIPTION,
    )


async def test_a_problem_card_holds_its_body_and_its_warnings():
    def problem(terminal):
        terminal.block("Kiln isn't running", ["Open it.", "  1. Install it."])
        terminal.warn("An eval was left out.")

    assert await show(lambda app: card_text(app, ".-problem"), problem) == (
        "✗ Kiln isn't running",
        "",
        "Open it.\n  1. Install it.\n! An eval was left out.",
    )


SUMMARY = f"\n{SUMMARY_HEADING}\n  Improve      Quality\n  Limits       $50.00"
RERUN = f"\n{RERUN_HEADING}\n  harness-tuner \\\n    --objective quality\n"


async def test_the_summary_card_holds_the_rerun_command():
    def summary(terminal):
        terminal.say(SUMMARY)
        terminal.say(RERUN)

    assert await show(lambda app: card_text(app, ".-summary"), summary) == (
        SUMMARY_HEADING,
        "",
        "Improve      Quality\n"
        "Limits       $50.00\n"
        "\n"
        f"{RERUN_HEADING}\n"
        "harness-tuner \\\n"
        "  --objective quality",
    )


async def test_a_rerun_command_with_no_summary_before_it_stands_alone():
    assert await show(lines, lambda t: t.say(RERUN)) == [
        f"{RERUN_HEADING}\nharness-tuner \\\n  --objective quality"
    ]


async def test_the_busy_line_shows_while_its_check_runs():
    checking = threading.Event()
    done = threading.Event()

    def busy(terminal):
        with terminal.busy("Checking the task is ready…"):
            checking.set()
            done.wait(10)
        terminal.step("ok", "Ready", "1 run config")
        return terminal.act("What next?", ACTIONS)

    async with asking_after(busy, checking) as pilot:
        await until(pilot, lambda: pilot.app.screen.query(BusyLine))
        line = rendered(pilot.app.screen.query_one(BusyLine))
        assert line[0] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        assert line[1:] == " Checking the task is ready…"
        done.set()
        await prompted(pilot)
        assert not pilot.app.screen.query(BusyLine)
        assert lines(pilot.app) == ["✓ Ready     1 run config"]


@asynccontextmanager
async def asking_after(ask, started: threading.Event):
    """The app over `ask`, once `started` is set, before any prompt."""
    harness = Asking(ask)
    async with harness.app.run_test(size=SIZE) as pilot:
        await until(pilot, started.is_set)
        yield pilot
        await pilot.press("ctrl+c")
        await until(pilot, lambda: harness.app.return_code is not None)


@pytest.mark.parametrize(
    "opened,said",
    [
        (True, "Opening https://kiln.tech/download ↗"),
        (False, "Open this link in your browser: https://kiln.tech/download ↗"),
    ],
)
async def test_open_url_says_whether_it_opened_the_page(monkeypatch, opened, said):
    tried = []

    def open_it(url):
        tried.append(url)
        return opened

    def open_page(terminal):
        terminal.open_url("https://kiln.tech/download")
        return terminal.act("What next?", ACTIONS)

    clicked = []
    monkeypatch.setattr(opener, "open_link", clicked.append)
    async with asking(open_page, opener=open_it) as (_harness, pilot):
        assert lines(pilot.app) == [said]
        # The link is clickable too.
        line = pilot.app.screen.query_one(StepLineView)
        await pilot.click(line, offset=(len(said) - 5, 0))
        await until(pilot, lambda: clicked)
    assert tried == ["https://kiln.tech/download"]
    assert clicked == ["https://kiln.tech/download"]


async def test_the_title_bar_names_the_task_once_it_is_chosen():
    def chosen(terminal):
        terminal.step("ok", "Project", "Forum Moderation (424242975950)")
        terminal.step("ok", "Task", "Moderate posts (424242783061) · the only task")
        return terminal.act("What next?", ACTIONS)

    async with asking(chosen) as (_harness, pilot):
        context = pilot.app.screen.query_one(TitleBar).query_one(".title-context")
        assert rendered(context) == "Moderate posts"


class StepRecorder:
    def __init__(self):
        self.texts = []

    def step(self, _status, _label, text):
        self.texts.append(text)


@pytest.mark.parametrize("note", ["", "the only task in Forum (old)"])
@pytest.mark.parametrize("name", ["Moderate posts", "Spam (v2) · triage"])
def test_task_name_reads_the_name_the_task_step_shows(name, note):
    recorder = StepRecorder()
    row = TaskRow(id="424242783061", name=name)
    _step(recorder, "Task", row, note=note)
    assert task_name(recorder.texts[0]) == name


async def test_the_newest_part_is_in_view():
    def many(terminal):
        for i in range(80):
            terminal.step("ok", "Step", f"line {i}")
        return terminal.act("What next?", ACTIONS)

    async with asking(many) as (_harness, pilot):
        transcript = pilot.app.screen.query_one("#transcript")
        await until(pilot, lambda: transcript.max_scroll_y > 0)
        await until(pilot, lambda: transcript.scroll_y == transcript.max_scroll_y)


async def test_no_color_turns_the_colors_off_and_the_glyphs_stay(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    def steps(terminal):
        terminal.step("ok", "Kiln", "1.2.0")
        terminal.warn("An eval was left out.")

    harness = Asking(lambda t: (steps(t), t.act("What next?", ACTIONS)))
    assert harness.app.no_color
    async with harness.app.run_test(size=SIZE) as pilot:
        await prompted(pilot)
        assert lines(pilot.app) == ["✓ Kiln      1.2.0", "! An eval was left out."]
        await pilot.press("ctrl+c")


# ---- nothing is cut off (functional spec section 8) ----

LONG_GUIDANCE = (
    "Keep the output format exactly as it is and never switch models; try the"
    " prompt first, then the tools, then the skills, and stop there END-GUIDANCE"
)
LONG_TITLE = "Moderate posts on the gardening forum isn't ready yet END-TITLE"


def screen_text(app) -> str:
    """The screen as it is drawn, one line per row. Textual has no public
    way to read it back but an SVG; the compositor holds the rows."""
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def long_texts(terminal):
    terminal.welcome("Kiln Harness Tuner", "0.1.0", WELCOME_DESCRIPTION)
    terminal.block(LONG_TITLE, ["short"])
    terminal.block(
        "Kiln isn't running",
        [
            "The tuner starts from one of the task's run configs and measures"
            " every change with its evals, and this line is long END-BODY"
        ],
    )
    terminal.warn(
        "Kiln can't load an eval: its judge model is retired, so the run leaves"
        " it out; to use it, fix its judge in Kiln END-WARN"
    )
    terminal.say(f'\n{SUMMARY_HEADING}\n  Guidance     "{LONG_GUIDANCE}"')
    terminal.say(
        f"\n{RERUN_HEADING}\n  harness-tuner \\\n    --guidance '{LONG_GUIDANCE}'\n"
    )


@pytest.mark.parametrize("width", [210, 120, 100, 60])
async def test_long_lines_in_cards_wrap_and_titles_show_whole(width):
    def start(terminal):
        long_texts(terminal)
        return terminal.act("Start the run?", ACTIONS)

    harness = Asking(start)
    async with harness.app.run_test(size=(width, 140)) as pilot:
        await prompted(pilot)
        await pilot.pause(0.1)
        text = screen_text(pilot.app)
        await pilot.press("ctrl+c")
    for marker in ("END-BODY", "END-WARN", "Kiln Harness Tuner", "v0.1.0"):
        assert marker in text
    assert text.count("END-GUIDANCE") == 2  # the summary and the rerun command
    if width >= len(LONG_TITLE) + 12:
        assert f"─ ✗ {LONG_TITLE} ─" in text
    else:
        # Too wide for the border at 60 columns: it opens the card's text.
        assert "│ ✗ Moderate posts" in text and "END-TITLE" in text
    assert max(len(line.rstrip()) for line in text.splitlines()) <= width


@pytest.mark.parametrize("width", [120, 60])
async def test_a_long_description_and_a_long_refusal_wrap(width):
    described = [
        Option(
            "p1",
            "Forum",
            description="The demo: it moderates the posts of an invented gardening"
            " forum, with its evals and data already set up END-DESCRIPTION",
        )
    ]
    async with asking(pick(described), size=(width, 40)) as (_harness, pilot):
        await pilot.pause(0.1)
        assert "END-DESCRIPTION" in screen_text(pilot.app)

    def refuse(_answer):
        return (
            "Enter a number of US dollars, such as 50 or 12.5, or none for no"
            " cap on what the run may spend END-REFUSAL"
        )

    async with asking(text(validate=refuse), size=(width, 40)) as (harness, pilot):
        assert await still_asking(harness, pilot, "enter")
        assert "END-REFUSAL" in screen_text(pilot.app)
