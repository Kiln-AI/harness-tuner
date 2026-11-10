import os
import os as _o
import sys

from ptyharness import HERE, PY
from ptyharness import run as _run

PYX = _o.environ.get("PYX", PY)


def run(*a, **k):
    return _run(*a, python=PYX, **k)


TRACE = os.path.join(HERE, "e4_trace.log")
only = sys.argv[1:] or None

SCEN = {
    "A normal": (["none"], [(1.0, b"a"), (1.6, b"a")], {}),
    "B ctrl+c confirm": (
        ["none"],
        [(1.0, b"a"), (1.6, b"a"), (3.0, b"\x03"), (3.4, b"y")],
        {},
    ),
    "C SIGTERM while run loop blocked": (
        ["none"],
        [(1.0, b"a"), (1.6, b"a"), (3.2, "SIGTERM")],
        {"E4_BLOCK_S": "3"},
    ),
    "D terminal closed (SIGHUP)": (
        ["none"],
        [(1.0, b"a"), (1.6, b"a")],
        {"_close": 3.0},
    ),
    "E caught UI fault": (["caught"], [(1.0, b"a"), (1.6, b"a")], {}),
    "F uncaught UI fault": (["uncaught"], [(1.0, b"a"), (1.6, b"a")], {}),
    "G ctrl+c during a start-up question": (
        ["none"],
        [(1.0, b"\x03"), (1.4, b"y")],
        {},
    ),
    "H kill -INT during run": (
        ["none"],
        [(1.0, b"a"), (1.6, b"a"), (2.5, "SIGINT")],
        {},
    ),
}

for name, (args, steps, env) in SCEN.items():
    if only and not any(name.startswith(o) for o in only):
        continue
    env = dict(env)
    close = env.pop("_close", None)
    res = run(
        ["e4_arch.py", *args], steps=steps, timeout=25, env=env, close_master_at=close
    )
    before, inside, after = res.segments()
    print(
        f"=== {name}: exit={res.exit_code()} timed_out={res.timed_out} elapsed={res.elapsed:.1f}s"  # noqa: E501
    )
    tail = after.replace("\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l", "")
    tail = tail.replace("\x1b[?1049l\x1b[?25h\x1b[?1004l", "<alt-off>")
    print(
        "   after alt screen:",
        repr(tail[:400])
        if "Traceback" not in tail
        else f"<traceback printed, {len(tail)} chars> ... {tail[-200:]!r}",
    )
    if os.path.exists(TRACE):
        lines = open(TRACE).read().splitlines()
        t0 = float(lines[0].split()[0])
        for ln in lines:
            ts, rest = ln.split(" ", 1)
            if "run: wave" in rest:
                continue
            print(f"   {float(ts) - t0:5.2f} {rest}")
        os.remove(TRACE)
