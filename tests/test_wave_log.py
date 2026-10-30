"""wave_log.py — the append-only JSONL audit trail + the FanoutLog glue."""

import json
from datetime import datetime

from conftest import RecordingLog

from kiln_harness_tuner.schemas import ErrorInfo
from kiln_harness_tuner.wave_log import FanoutLog, WaveLog

FIXED = datetime(2026, 7, 23, 14, 2, 0)


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_one_json_line_per_event_with_ts_and_fields(tmp_path):
    path = tmp_path / "runs" / "260723-1402" / "wave_log.jsonl"
    with WaveLog(path, now=lambda: FIXED) as log:
        log.event("wave_start", wave=1, should_wrap_up=False)
        log.event("exit", reason="wrap_up")
    lines = read_lines(path)
    assert lines == [
        {
            "ts": "2026-07-23T14:02:00",
            "kind": "wave_start",
            "wave": 1,
            "should_wrap_up": False,
        },
        {"ts": "2026-07-23T14:02:00", "kind": "exit", "reason": "wrap_up"},
    ]


def test_pydantic_models_and_nested_values_serialize(tmp_path):
    path = tmp_path / "wave_log.jsonl"
    with WaveLog(path, now=lambda: FIXED) as log:
        log.event(
            "item_outcome",
            item_id="w1-1",
            outcome=ErrorInfo(error_class="timeout", detail="d"),
            items=[{"item_id": "w1-1"}, ErrorInfo(error_class="timeout", detail="e")],
            progress=(12, 20),
        )
    (line,) = read_lines(path)
    assert line["outcome"] == {"error_class": "timeout", "detail": "d"}
    assert line["items"] == [
        {"item_id": "w1-1"},
        {"error_class": "timeout", "detail": "e"},
    ]
    assert line["progress"] == [12, 20]


def test_non_json_values_fall_back_to_repr(tmp_path):
    path = tmp_path / "wave_log.jsonl"
    with WaveLog(path, now=lambda: FIXED) as log:
        log.event("odd", value={1, 2} if True else None)
    (line,) = read_lines(path)
    assert line["value"] in ("{1, 2}", "{2, 1}")  # repr'd, never a write failure


def test_append_mode_across_instances(tmp_path):
    path = tmp_path / "wave_log.jsonl"
    with WaveLog(path, now=lambda: FIXED) as log:
        log.event("run_start", timeout_mins=180)
    with WaveLog(path, now=lambda: FIXED) as log:
        log.event("run_start", timeout_mins=60)
    assert [line["timeout_mins"] for line in read_lines(path)] == [180, 60]


def test_fanout_forwards_to_all_children_in_order():
    order: list[str] = []

    class Named(RecordingLog):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def event(self, kind, **fields):
            order.append(self.name)
            super().event(kind, **fields)

    first, second = Named("first"), Named("second")
    FanoutLog(first, second).event("exit", reason="wrap_up")
    assert order == ["first", "second"]
    assert first.events == [("exit", {"reason": "wrap_up"})]
    assert second.events == [("exit", {"reason": "wrap_up"})]
