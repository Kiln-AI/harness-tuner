"""The start-up's side of the app (architecture 4.1, 4.2): `AppTerminal` puts
each `Terminal` call on the queue, and its prompts wait for an answer;
`CancellableRunner` runs Kiln reads that the app can stop. No Textual here:
the tests read the queue and answer the futures themselves."""

import asyncio
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from queue import SimpleQueue

import pytest

from kiln_harness_tuner.onboarding import demo
from kiln_harness_tuner.onboarding.console import PlainTerminal
from kiln_harness_tuner.onboarding.errors import GoBack, OnboardingStopped
from kiln_harness_tuner.onboarding.summary import RERUN_HEADING, SUMMARY_HEADING
from kiln_harness_tuner.onboarding.terminal import ENTER, Action, ActionKind, Option
from kiln_harness_tuner.tui import bridge
from kiln_harness_tuner.tui.bridge import (
    UI_REQUEST,
    AppTerminal,
    CancellableRunner,
    opened_text,
    said_kind,
)

OPTIONS = [Option("p1", "Forum Moderation"), Option("p2", "Support Triage")]
RETRY = Action("r", "Check again", ActionKind.RETRY)
EXIT = Action("x", "Exit", ActionKind.EXIT)


def requests(queue: SimpleQueue) -> list[bridge.Request]:
    found = []
    while not queue.empty():
        stamped = queue.get_nowait()
        assert stamped.kind == UI_REQUEST
        found.append(stamped.fields["request"])
    return found


def terminal(**kwargs) -> tuple[AppTerminal, SimpleQueue]:
    queue: SimpleQueue = SimpleQueue()
    return AppTerminal(queue, interactive=True, **kwargs), queue


def test_each_call_puts_its_request_in_order():
    term, queue = terminal()
    term.welcome("Kiln Harness Tuner", "0.1.0", "Tunes an agent.")
    term.step("ok", "Kiln", "1.2.0")
    term.block("Kiln isn't running", ["Open it."])
    term.warn("Careful.")
    term.say("Ready to start")
    with pytest.raises(RuntimeError, match="check failed"):
        with term.busy("Checking Kiln…"):
            raise RuntimeError("check failed")
    assert requests(queue) == [
        bridge.Welcome("Kiln Harness Tuner", "0.1.0", "Tunes an agent."),
        bridge.Step("ok", "Kiln", "1.2.0"),
        bridge.Block("Kiln isn't running", ("Open it.",)),
        bridge.Warn("Careful."),
        bridge.Say("Ready to start"),
        # The spinner ends also when its check raises.
        bridge.Busy("Checking Kiln…"),
        bridge.BusyEnd(),
    ]


def ask_on_a_thread(call):
    """`call()` on a thread; its result or error lands in the returned
    future."""
    result: Future = Future()

    def main():
        try:
            result.set_result(call())
        except BaseException as error:
            result.set_exception(error)

    threading.Thread(target=main, daemon=True).start()
    return result


def next_prompt(queue: SimpleQueue) -> bridge.Prompt:
    stamped = queue.get(timeout=5)
    request = stamped.fields["request"]
    assert isinstance(request, bridge.Prompt)
    return request


def test_say_hands_each_text_to_on_say_and_busy_names_its_check():
    said = []
    term, queue = terminal(on_say=said.append)
    term.say("Ready to start")
    assert said == ["Ready to start"]
    assert term.busy_text is None
    with term.busy(demo.COPYING):
        assert term.busy_text == demo.COPYING
        assert term.copying_demo
    with term.busy("Importing it into Kiln…"):
        assert not term.copying_demo
    assert term.busy_text is None
    assert not term.copying_demo


@pytest.mark.parametrize(
    "text,kind",
    [
        (f"\n{SUMMARY_HEADING}\n  Improve      Quality", "summary"),
        (f"\n{RERUN_HEADING}\n  harness-tuner\n", "rerun"),
        ("Opening https://kiln.tech/download", None),
    ],
)
def test_said_kind_reads_the_first_line(text, kind):
    assert said_kind(text) == kind


def test_a_prompt_waits_for_its_answer():
    term, queue = terminal()
    picked = ask_on_a_thread(lambda: term.pick("Which?", OPTIONS, hint="h"))
    prompt = next_prompt(queue)
    assert isinstance(prompt, bridge.Pick)
    assert (prompt.question, prompt.options, prompt.hint, prompt.default) == (
        "Which?",
        tuple(OPTIONS),
        "h",
        None,
    )
    assert not picked.done()
    prompt.resolve("p2")
    assert picked.result(5) == "p2"

    acted = ask_on_a_thread(lambda: term.act("What next?", [RETRY, EXIT]))
    next_prompt(queue).resolve(EXIT)
    assert acted.result(5) is EXIT

    def refuse(_text):
        return None

    typed = ask_on_a_thread(
        lambda: term.text("Budget?", default="50", hint="USD", validate=refuse)
    )
    prompt = next_prompt(queue)
    assert (prompt.question, prompt.default, prompt.hint) == ("Budget?", "50", "USD")
    assert prompt.validate is refuse
    prompt.fail(ValueError("bad policy"))
    with pytest.raises(ValueError, match="bad policy"):
        typed.result(5)


@pytest.mark.parametrize(
    "call",
    [
        lambda t, back: t.pick("Which?", OPTIONS, back=back),
        lambda t, back: t.act("What next?", [RETRY], back=back),
        lambda t, back: t.text("Budget?", back=back),
    ],
    ids=["pick", "act", "text"],
)
def test_each_prompt_says_whether_esc_goes_back(call):
    term, queue = terminal()
    for back in (False, True):
        asked = ask_on_a_thread(lambda back=back: call(term, back))
        prompt = next_prompt(queue)
        assert prompt.back is back
        prompt.fail(GoBack())
        with pytest.raises(GoBack):
            asked.result(5)


def test_mark_and_rewind_put_their_requests_in_order():
    term, queue = terminal()
    first = term.mark()
    term.step("ok", "Improve", "Quality")
    second = term.mark()
    term.rewind(first)
    assert first != second
    assert requests(queue) == [
        bridge.Mark(first),
        bridge.Step("ok", "Improve", "Quality"),
        bridge.Mark(second),
        bridge.Rewind(first),
    ]


def test_the_plain_terminal_marks_and_rewinds_nothing():
    plain = PlainTerminal("harness-tuner")
    plain.rewind(plain.mark())


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.pick("Which?", OPTIONS),
        lambda t: t.act("What next?", [RETRY]),
        lambda t: t.text("Budget?"),
        lambda t: t.block("Title", ["body"]),
        lambda t: t.open_url("https://kiln.tech/download"),
    ],
    ids=["pick", "act", "text", "block", "open_url"],
)
def test_a_start_that_isnt_guided_never_asks(call):
    queue: SimpleQueue = SimpleQueue()
    with pytest.raises(RuntimeError, match="not a guided start"):
        call(AppTerminal(queue, interactive=False))
    assert queue.empty()


def test_act_lets_only_the_first_action_be_enter():
    term, _queue = terminal()
    keep = Action(ENTER, "Keep these", ActionKind.KEEP)
    with pytest.raises(AssertionError, match="Enter picks the first action"):
        term.act("Advanced options", [RETRY, keep])


def test_abandon_fails_the_open_prompt_and_every_later_one():
    term, queue = terminal()
    picked = ask_on_a_thread(lambda: term.pick("Which?", OPTIONS))
    prompt = next_prompt(queue)
    term.abandon(OnboardingStopped(130))
    with pytest.raises(OnboardingStopped) as stopped:
        picked.result(5)
    assert stopped.value.exit_code == 130
    # An answer that comes after the stop changes nothing.
    prompt.resolve("p1")
    # The first stop wins, and a later prompt raises it at once.
    term.abandon(OnboardingStopped(1))
    with pytest.raises(OnboardingStopped) as later:
        term.act("What next?", [RETRY])
    assert later.value.exit_code == 130
    assert queue.empty()


@pytest.mark.parametrize("opened", [True, False])
def test_open_url_says_whether_the_page_opened(opened):
    tried = []

    def opener(url):
        tried.append(url)
        return opened

    term, queue = terminal(opener=opener)
    term.open_url("https://kiln.tech/download")
    assert tried == ["https://kiln.tech/download"]
    assert requests(queue) == [bridge.Opened("https://kiln.tech/download", opened)]


def test_opened_text_is_the_guided_starts_own():
    url = "https://kiln.tech/download"
    assert opened_text(url, True) == f"Opening {url}"
    assert opened_text(url, False) == f"Open this link in your browser: {url}"


# ---- the runner ----


class FakeKiln:
    """A Kiln client: `call` answers at once, or waits until cancelled."""

    def __init__(self, *, stuck=False):
        self.stuck = stuck
        self.calling = threading.Event()
        self.opened = 0
        self.closed = 0
        self.cancelled = False

    async def call(self, method, path, *_args, **_kwargs):
        self.calling.set()
        if self.stuck:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return f"{method} {path}"

    def connect(self):
        @asynccontextmanager
        async def connected():
            self.opened += 1
            try:
                yield self
            finally:
                self.closed += 1

        return connected()


def ping(kiln):
    return kiln.call("GET", "/ping")


def test_the_runner_returns_what_the_call_returns_and_closes_the_client():
    kiln = FakeKiln()
    runner = CancellableRunner(kiln.connect)
    assert runner(ping) == "GET /ping"
    assert runner(ping) == "GET /ping"
    # A fresh client per call, as `kiln_runner` gives.
    assert (kiln.opened, kiln.closed) == (2, 2)


def test_cancel_stops_the_call_in_flight_with_keyboard_interrupt():
    kiln = FakeKiln(stuck=True)
    runner = CancellableRunner(kiln.connect)
    called = ask_on_a_thread(lambda: runner(ping))
    assert kiln.calling.wait(5)
    runner.cancel()
    with pytest.raises(KeyboardInterrupt):
        called.result(5)
    assert kiln.cancelled
    assert kiln.closed == 1
    # And every later call, at once, with no client.
    with pytest.raises(KeyboardInterrupt):
        runner(ping)
    assert kiln.opened == 1


def test_a_cancel_with_nothing_in_flight_stops_the_next_call():
    kiln = FakeKiln()
    runner = CancellableRunner(kiln.connect)
    runner.cancel()
    with pytest.raises(KeyboardInterrupt):
        runner(ping)
    assert kiln.opened == 0


def test_a_cancelled_error_of_the_call_itself_is_not_a_stop():
    async def cancelled(_kiln):
        raise asyncio.CancelledError

    runner = CancellableRunner(FakeKiln().connect)
    with pytest.raises(asyncio.CancelledError):
        runner(cancelled)
