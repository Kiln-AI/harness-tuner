"""The owner questions dialog in the app (`tui/owner.py`,
`tui/screens/owner.py`): a run that asks the owner waits for the dialog's
answer, for a limited time. Pilot tests at 210×62, on fake threads."""

import asyncio
import signal
from queue import SimpleQueue

from app_fakes import SIZE, FakeRun, make_app, make_session, until
from test_app import RUN_START, text
from textual.content import Content
from textual.widgets import Input, OptionList, Static

from kiln_harness_tuner.owner_questions import (
    DISPLAY_CLOSED,
    LEFT_FOR_LATER,
    NO_ANSWER_IN_TIME,
    OwnerReplies,
    OwnerReply,
)
from kiln_harness_tuner.schemas import OwnerOption, OwnerQuestion
from kiln_harness_tuner.tui import owner
from kiln_harness_tuner.tui.owner import (
    OWNER_ASK,
    OWNER_ASK_CLOSED,
    AppOwnerAsker,
    OwnerAsk,
)
from kiln_harness_tuner.tui.screens.owner import OwnerQuestionsDialog
from kiln_harness_tuner.tui.screens.stop import StopDialog

REFUNDS = OwnerQuestion(
    question="Which rule about refunds wins?",
    why="The prompt says to refund at once. The skill `refunds` says to ask first.",
    smell="conflicting-requirements",
    options=[
        OwnerOption(label="Leave both texts as they are", effect="No change."),
        OwnerOption(label="The prompt wins", effect="The skill is changed."),
        OwnerOption(label="The skill wins", effect="The prompt is changed."),
    ],
)
TONE = OwnerQuestion(
    question="Is a short answer or a full answer correct?",
    options=[OwnerOption(label="Short"), OwnerOption(label="Full")],
    default=1,
)


class AskingRun(FakeRun):
    """A run that asks the owner through the app's asker, as the loop does
    before wave 1, and keeps what it got."""

    def __init__(self, questions, *, wait_secs: float = 30.0):
        super().__init__([RUN_START])
        self.questions = list(questions)
        self.wait_secs = wait_secs
        self.asker: AppOwnerAsker | None = None
        self.got: OwnerReplies | None = None

    async def _main(self) -> str:
        assert self.queue is not None
        self.asker = AppOwnerAsker(self.queue)
        asking = asyncio.create_task(self._ask())
        try:
            return await super()._main()
        finally:
            asking.cancel()

    async def _ask(self) -> None:
        # After the run's own events, as the step comes after `run_start`.
        await asyncio.sleep(0.05)
        assert self.asker is not None
        self.got = await self.asker(self.questions, self.wait_secs)


def asking_app(*questions, **kwargs):
    run = AskingRun(questions, **kwargs)
    session = make_session(run=run)
    return make_app(session), session, run


async def dialog(pilot) -> OwnerQuestionsDialog:
    """The dialog, once it shows its first question with the focus on its
    options."""

    def ready() -> bool:
        screen = pilot.app.screen
        if not isinstance(screen, OwnerQuestionsDialog):
            return False
        options = screen.query("#owner-options")
        shown_question = screen.query("#owner-question")
        return (
            bool(options)
            and options.first().has_focus
            and bool(shown_question)
            and bool(str(shown_question.first().render()))
            and bool(screen.query_one("#owner-dialog").border_title)
        )

    await until(pilot, ready)
    return pilot.app.screen


def shown(screen: OwnerQuestionsDialog, part: str) -> str:
    return str(screen.query_one(f"#owner-{part}", Static).render())


async def choose(pilot, key: str) -> None:
    """Move the cursor with `key`, then press Enter. The pause lets the
    option list take the move before the Enter."""
    await pilot.press(key)
    await pilot.pause(0.1)
    await pilot.press("enter")


def title(screen: OwnerQuestionsDialog) -> str:
    return Content.from_markup(
        str(screen.query_one("#owner-dialog").border_title)
    ).plain


def rows(screen: OwnerQuestionsDialog) -> list[str]:
    options = screen.query_one("#owner-options", OptionList)
    count = options.option_count
    return [str(options.get_option_at_index(i).prompt) for i in range(count)]


# ---- what the dialog shows ----


async def test_the_dialog_shows_the_question_its_options_and_other():
    app, session, run = asking_app(REFUNDS)
    async with app.run_test(size=SIZE) as pilot:
        screen = await dialog(pilot)
        assert title(screen) == "A question for you (1 of 1)"
        assert shown(screen, "question") == REFUNDS.question
        assert shown(screen, "why") == REFUNDS.why
        assert rows(screen) == [
            "Leave both texts as they are (default)\n  No change.",
            "The prompt wins\n  The skill is changed.",
            "The skill wins\n  The prompt is changed.",
            "Other: type your own answer",
        ]
        assert "10 min" not in shown(screen, "hint")
        assert "With no answer in 1 min" in shown(screen, "hint")
        # The default has the cursor, and the text input is not shown.
        options = screen.query_one("#owner-options", OptionList)
        assert options.highlighted == 0 and options.has_focus
        assert not screen.query_one("#owner-other", Input).display
        session.run.stop(signal.SIGINT)


async def test_the_cursor_starts_on_the_default_option():
    app, session, run = asking_app(TONE)
    async with app.run_test(size=SIZE) as pilot:
        screen = await dialog(pilot)
        assert screen.query_one("#owner-options", OptionList).highlighted == 1
        assert not screen.query_one("#owner-why", Static).display
        session.run.stop(signal.SIGINT)


# ---- the answers ----


async def test_enter_on_an_option_answers_and_the_run_goes_on():
    app, session, run = asking_app(REFUNDS)
    async with app.run_test(size=SIZE) as pilot:
        await dialog(pilot)
        await choose(pilot, "down")
        await until(pilot, lambda: run.got is not None)
        assert run.got == OwnerReplies((OwnerReply("option", option=1),), "")
        await until(pilot, lambda: not isinstance(app.screen, OwnerQuestionsDialog))
        assert app.screen is app.run_screen
        session.run.stop(signal.SIGINT)


async def test_other_takes_the_owners_own_words():
    app, session, run = asking_app(REFUNDS)
    async with app.run_test(size=SIZE) as pilot:
        screen = await dialog(pilot)
        await choose(pilot, "end")
        other = screen.query_one("#owner-other", Input)
        await until(pilot, lambda: other.has_focus)
        # Enter with no text answers nothing.
        await pilot.press("enter")
        assert run.got is None
        # Spaces inside the text are made one.
        await pilot.press(*"Ask  first", "enter")
        await until(pilot, lambda: run.got is not None, timeout=15.0)
        assert run.got.replies == (OwnerReply("other", text="Ask first"),)
        session.run.stop(signal.SIGINT)


async def test_escape_in_the_text_goes_back_to_the_options():
    app, session, run = asking_app(REFUNDS)
    async with app.run_test(size=SIZE) as pilot:
        screen = await dialog(pilot)
        await choose(pilot, "end")
        other = screen.query_one("#owner-other", Input)
        await until(pilot, lambda: other.has_focus)
        await pilot.press("escape")
        await until(pilot, lambda: not other.display)
        assert isinstance(app.screen, OwnerQuestionsDialog)
        assert run.got is None
        session.run.stop(signal.SIGINT)


async def test_two_questions_are_asked_one_after_the_other():
    app, session, run = asking_app(REFUNDS, TONE)
    async with app.run_test(size=SIZE) as pilot:
        screen = await dialog(pilot)
        await pilot.press("enter")
        await until(pilot, lambda: shown(screen, "question") == TONE.question)
        assert title(screen) == "A question for you (2 of 2)"
        assert run.got is None
        await choose(pilot, "up")
        await until(pilot, lambda: run.got is not None)
        assert run.got.replies == (
            OwnerReply("option", option=0),
            OwnerReply("option", option=0),
        )
        session.run.stop(signal.SIGINT)


async def test_escape_leaves_this_question_and_the_next_ones_for_later():
    app, session, run = asking_app(REFUNDS, TONE)
    async with app.run_test(size=SIZE) as pilot:
        screen = await dialog(pilot)
        await pilot.press("enter")
        await until(pilot, lambda: shown(screen, "question") == TONE.question)
        await pilot.press("escape")
        await until(pilot, lambda: run.got is not None)
        assert run.got == OwnerReplies(
            (OwnerReply("option", option=0), None), LEFT_FOR_LATER
        )
        await until(pilot, lambda: app.screen is app.run_screen)
        session.run.stop(signal.SIGINT)


# ---- the wait ends by itself ----


async def test_the_dialog_goes_away_when_the_wait_is_over():
    app, session, run = asking_app(REFUNDS, wait_secs=2.0)
    async with app.run_test(size=SIZE) as pilot:
        await dialog(pilot)
        await until(pilot, lambda: run.got is not None)
        assert run.got == OwnerReplies((None,), NO_ANSWER_IN_TIME)
        await until(pilot, lambda: app.screen is app.run_screen)
        # The run goes on: the app is still open.
        assert app.return_code is None
        session.run.stop(signal.SIGINT)


async def test_an_expired_dialog_under_the_stop_dialog_goes_when_it_is_on_top():
    app, session, run = asking_app(REFUNDS, wait_secs=2.0)
    async with app.run_test(size=SIZE) as pilot:
        await dialog(pilot)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: isinstance(app.screen, StopDialog))
        await until(pilot, lambda: run.got is not None)
        # "Keep tuning": the question is over, so its dialog is not shown.
        await pilot.press("escape")
        await until(pilot, lambda: app.screen is app.run_screen)
        session.run.stop(signal.SIGINT)


async def test_a_dialog_that_expires_at_once_does_not_break_the_app():
    app, session, run = asking_app(REFUNDS, wait_secs=0.0)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: run.got is not None)
        assert run.got == OwnerReplies((None,), NO_ANSWER_IN_TIME)
        await until(pilot, lambda: app.screen is app.run_screen)
        await pilot.pause(0.2)
        assert app.return_code is None
        session.run.stop(signal.SIGINT)


async def test_a_stopped_run_takes_its_dialog_away():
    app, session, run = asking_app(REFUNDS)
    async with app.run_test(size=SIZE) as pilot:
        await dialog(pilot)
        session.run.stop(signal.SIGINT)
        await until(pilot, lambda: app.return_code is not None)
    assert run.got is None and run.cancelled


async def test_the_run_screen_logs_the_step_line():
    line = "Questions for the owner: 1 answered."
    run = FakeRun([RUN_START, ("owner_questions", {"line": line})])
    session = make_session(run=run)
    app = make_app(session)
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.run_screen is not None and app.model is not None)
        await until(pilot, lambda: line in text(app, "log"))
        session.run.stop(signal.SIGINT)


# ---- the asker, with no app ----


async def test_a_closed_asker_answers_nothing_at_once():
    queue: SimpleQueue = SimpleQueue()
    asker = AppOwnerAsker(queue)
    asker.close()
    assert await asker([REFUNDS, TONE], 30.0) == OwnerReplies.none(2, DISPLAY_CLOSED)
    assert queue.empty()


async def test_closing_the_asker_ends_the_open_ask():
    queue: SimpleQueue = SimpleQueue()
    asker = AppOwnerAsker(queue)
    asking = asyncio.create_task(asker([REFUNDS], 30.0))
    await asyncio.sleep(0.01)
    asker.close()
    assert await asking == OwnerReplies((None,), DISPLAY_CLOSED)
    assert [queue.get_nowait().kind for _ in range(2)] == [OWNER_ASK, OWNER_ASK_CLOSED]


def test_an_ask_keeps_how_it_ended():
    ask = OwnerAsk((REFUNDS,), 30.0)
    ask.abandon(DISPLAY_CLOSED)
    ask.resolve([OwnerReply("option", option=1)])
    assert ask.answer.result() == OwnerReplies((None,), DISPLAY_CLOSED)


def test_the_hint_names_the_wait_in_minutes():
    assert "in 10 min" in owner.dialog_hint(600.0)
