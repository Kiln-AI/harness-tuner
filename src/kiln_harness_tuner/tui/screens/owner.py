"""The owner questions dialog: what only the owner of the agent can decide,
asked one time, before wave 1 (`kiln_harness_tuner.owner_questions`).

A card over the dimmed run screen, as the stop dialog. It shows one question
at a time: the question, why it is asked, its options with the default
marked, and "Other" for an answer in the owner's own words. Enter chooses.
Esc, or a click outside the card, leaves this question and the next ones for
later. The result is one reply for each question, and None for each one left
for later.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option as Row

from kiln_harness_tuner.owner_questions import OWNER_OTHER_MAX_CHARS, OwnerReply
from kiln_harness_tuner.schemas import OwnerQuestion
from kiln_harness_tuner.tui import owner
from kiln_harness_tuner.tui.owner import OwnerAsk
from kiln_harness_tuner.tui.screens.modal import CardModal
from kiln_harness_tuner.tui.screens.prompts import PromptInput
from kiln_harness_tuner.tui.widgets import Card


def option_rows(question: OwnerQuestion) -> list[Content]:
    """One row for each option, with what it does and the default's mark,
    then the "Other" row."""
    rows = []
    for index, option in enumerate(question.options):
        parts = [Content.styled(option.label, "bold")]
        if index == question.default:
            parts.append(Content.styled(f" ({owner.DEFAULT_MARK})", "$text-muted"))
        if option.effect:
            parts.append(Content(f"\n  {option.effect}"))
        rows.append(Content.assemble(*parts))
    other = Content.assemble(
        Content.styled(owner.OTHER_LABEL, "bold"),
        Content.styled(f": {owner.OTHER_HELP}", "$text-muted"),
    )
    return [*rows, other]


class OwnerQuestionsDialog(CardModal[list[OwnerReply | None]]):
    BINDINGS = [Binding("escape", "later", owner.LATER)]

    def __init__(self, ask: OwnerAsk):
        super().__init__()
        self.ask = ask
        self.replies: list[OwnerReply | None] = []
        # The run stopped waiting for this dialog (`TunerApp`).
        self.expired = False
        # The dialog has answered, or the app has taken it away.
        self.gone = False

    @property
    def question(self) -> OwnerQuestion:
        return self.ask.questions[len(self.replies)]

    def compose(self) -> ComposeResult:
        with Card(id="owner-dialog"):
            yield Static(markup=False, id="owner-question")
            yield Static(markup=False, id="owner-why")
            yield OptionList(id="owner-options")
            yield PromptInput(
                placeholder=owner.OTHER_PLACEHOLDER,
                max_length=OWNER_OTHER_MAX_CHARS,
                id="owner-other",
            )
            yield Static(
                owner.dialog_hint(self.ask.wait_secs), markup=False, id="owner-hint"
            )
        # A modal screen has its own bindings, so it needs its own footer.
        yield Footer()

    def on_mount(self) -> None:
        # After the first refresh: the card's own children can mount after
        # the screen does.
        self.call_after_refresh(self._show)

    def _show(self) -> None:
        """The question that has no reply yet. Nothing when the dialog has
        gone: the run can stop waiting before the first refresh."""
        if self.gone or self.expired or not self.is_attached:
            return
        question = self.question
        card = self.query_one("#owner-dialog", Card)
        title = owner.dialog_title(len(self.replies) + 1, len(self.ask.questions))
        card.border_title = Content.styled(title, "bold")
        self.query_one("#owner-question", Static).update(question.question)
        why = self.query_one("#owner-why", Static)
        why.update(question.why)
        why.display = bool(question.why)
        options = self.query_one("#owner-options", OptionList)
        options.clear_options()
        options.add_options(
            Row(row, id=str(i)) for i, row in enumerate(option_rows(question))
        )
        options.highlighted = question.default
        other = self.query_one("#owner-other", Input)
        other.value = ""
        other.display = False
        options.focus()

    @property
    def typing(self) -> bool:
        """The owner types an "Other" answer."""
        return bool(self.query_one("#owner-other", Input).display)

    @on(OptionList.OptionSelected, "#owner-options")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_index < len(self.question.options):
            self._reply(OwnerReply("option", option=event.option_index))
            return
        other = self.query_one("#owner-other", Input)
        other.display = True
        other.focus()
        self.refresh_bindings()

    @on(Input.Submitted, "#owner-other")
    def _typed(self, event: Input.Submitted) -> None:
        event.stop()
        text = " ".join(event.value.split())
        if text:
            self._reply(OwnerReply("other", text=text))

    def _reply(self, reply: OwnerReply) -> None:
        self.replies.append(reply)
        if len(self.replies) < len(self.ask.questions):
            self._show()
            self.refresh_bindings()
        else:
            self._answer()

    def _answer(self) -> None:
        if self.gone:
            return
        self.gone = True
        missing = len(self.ask.questions) - len(self.replies)
        self.dismiss([*self.replies, *([None] * missing)])

    def action_later(self) -> None:
        """Esc: from the "Other" text, back to the options. From the
        options, leave this question and the next ones for later."""
        if self.typing:
            self.query_one("#owner-other", Input).display = False
            self.query_one("#owner-options", OptionList).focus()
            self.refresh_bindings()
        else:
            self._answer()
