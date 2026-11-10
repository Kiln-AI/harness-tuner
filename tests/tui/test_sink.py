"""The run's display in the app (architecture section 5.2)."""

import logging
import time
from queue import SimpleQueue

from kiln_harness_tuner.status import RunStatusTracker
from kiln_harness_tuner.tui.closing import ClosingBlock
from kiln_harness_tuner.tui.sink import TuiSink
from kiln_harness_tuner.ui import plain_exit_line
from kiln_harness_tuner.wave_log import FanoutLog


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def sink_and_tracker(clock: Clock | None = None):
    tracker = RunStatusTracker(
        project_id="p1", task_id="t1", guidance="", clock=clock or Clock()
    )
    queue = SimpleQueue()
    closing = ClosingBlock()
    return TuiSink(tracker, queue, closing), tracker, queue, closing


class RecordingLog:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def test_each_event_goes_on_the_queue_with_its_stamp():
    sink, _tracker, queue, _closing = sink_and_tracker()
    before = time.monotonic()
    sink.event("phase_start", name="setup", label="Setup…")
    stamped = queue.get_nowait()
    assert stamped.kind == "phase_start"
    assert stamped.fields == {"name": "setup", "label": "Setup…"}
    assert before <= stamped.mono <= time.monotonic()
    with sink as entered:
        assert entered is sink


def test_the_exit_line_is_the_plain_line_at_the_exit_event():
    clock = Clock()
    sink, tracker, _queue, closing = sink_and_tracker(clock)
    log = FanoutLog(tracker, sink)
    log.event("run_start", timeout_mins=60, deadline=clock.t + 3600)
    clock.t += 125
    log.event("exit", reason="time_limit")
    expected = plain_exit_line({"reason": "time_limit"}, tracker.snapshot())
    assert closing.run_exit_line == expected
    assert "2m05s elapsed" in expected
    # The totals keep moving after `exit`; the line does not.
    clock.t += 600
    log.event("spend", source="report", usd=1.0)
    assert closing.run_exit_line == expected


def test_the_report_path_is_kept():
    sink, _tracker, _queue, closing = sink_and_tracker()
    sink.event("report_written", path="/reports/kiln-harness-tuner-1.md")
    assert closing.report_path == "/reports/kiln-harness-tuner-1.md"


def test_a_failing_display_never_raises_into_the_loop(caplog):
    sink, *_ = sink_and_tracker()

    class BrokenQueue:
        def put(self, _item):
            raise RuntimeError("the queue is gone")

    sink._queue = BrokenQueue()
    with caplog.at_level(logging.ERROR, logger="kiln_harness_tuner.tui"):
        sink.event("wave_start", wave=1)
    assert "failed on a wave_start event" in caplog.text


def test_after_the_app_closes_the_events_go_to_the_plain_display():
    sink, _tracker, queue, closing = sink_and_tracker()
    plain = RecordingLog()
    assert not sink.forwarding
    sink.forward_to(plain)
    assert sink.forwarding
    sink.event("wave_start", wave=2)
    sink.event("exit", reason="budget")
    assert plain.events == [("wave_start", {"wave": 2}), ("exit", {"reason": "budget"})]
    assert queue.empty()
    assert closing.run_exit_line.startswith("exit: budget — total")
