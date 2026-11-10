"""E4: a prototype of the leading architecture.

- Textual App.run() on the main thread.
- A start-up thread (daemon) runs a synchronous flow that blocks on the UI for
  each question (a concurrent.futures.Future the UI resolves).
- Then a run thread with its own asyncio.run(). Its loop sometimes blocks its
  own thread (time.sleep), like the real loop does for up to 60 s. It sends
  events into a queue.SimpleQueue; a UI interval timer drains it.
- SIGTERM/SIGHUP/SIGINT: loop.add_signal_handler on the *app's* loop (main
  thread) -> run_loop.call_soon_threadsafe(run_task.cancel).
- Ctrl+C / q -> confirm screen -> cancel the run the same way; the UI waits
  for the run thread to finish its finally blocks, then exits with 130.
- After App.run(): print a closing block to the normal scrollback.

argv[1] selects a UI fault to inject: "none", "caught" (exception inside a
guarded refresh), "uncaught" (exception escapes a timer callback).
Writes a trace to e4_trace.log (the 'run log').
"""

import asyncio
import concurrent.futures
import os
import queue
import signal
import sys
import threading
import time

from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE = os.path.join(HERE, "e4_trace.log")
FAULT = sys.argv[1] if len(sys.argv) > 1 else "none"
BLOCK_S = float(os.environ.get("E4_BLOCK_S", "2.0"))


def trace(*a):
    with open(TRACE, "a") as f:
        f.write(
            f"{time.monotonic():.2f} [{threading.current_thread().name}] "
            + " ".join(map(str, a))
            + "\n"
        )


# ---------------------------------------------------------------- run side
class RunHandle:
    """What the UI holds to stop the run; all methods are thread-safe."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self.ready = threading.Event()
        self.outcome: str | None = None
        self.thread: threading.Thread | None = None

    def cancel(self):
        self.ready.wait()
        if self.loop is not None and self.task is not None:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:  # loop already closed
                pass


async def fake_run(events: queue.SimpleQueue):
    try:
        for wave in range(1, 12):
            events.put(("wave", wave))
            trace("run: wave", wave)
            await asyncio.sleep(0.2)
            if wave % 5 == 0:
                trace(f"run: blocking own thread {BLOCK_S}s")
                time.sleep(BLOCK_S)  # the loop blocks itself, like reconcile()
        return "report.md"
    finally:
        trace("run: finally (children killed, lock released)")
        events.put(("finally", None))


def run_thread_main(handle: RunHandle, events: queue.SimpleQueue, on_done):
    async def main():
        handle.loop = asyncio.get_running_loop()
        handle.task = asyncio.current_task()
        handle.ready.set()
        return await fake_run(events)

    try:
        result = asyncio.run(main())
        handle.outcome = f"finished:{result}"
    except asyncio.CancelledError:
        handle.outcome = "cancelled"
    except BaseException as e:  # noqa: BLE001
        handle.outcome = f"error:{type(e).__name__}"
    finally:
        handle.ready.set()
        trace("run thread: outcome", handle.outcome)
        on_done()


# ------------------------------------------------------------- start-up side
class StartupStopped(Exception):
    pass


def startup_flow(ask) -> dict:
    """Synchronous, like prepare_start(): blocks on the UI for each answer."""
    trace("startup: asking the first question")
    a = ask("Which project?")
    trace("startup: answer", a)
    time.sleep(0.2)  # some synchronous work (Kiln calls)
    b = ask("Start the run?")
    trace("startup: answer", b)
    return {"project": a, "go": b}


# --------------------------------------------------------------------- UI
class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes"), Binding("n,escape", "no")]

    def compose(self):
        yield Static("Stop the run? y/n")

    def action_yes(self):
        self.dismiss(True)

    def action_no(self):
        self.dismiss(False)


class TunerApp(App[int]):
    BINDINGS = [
        Binding("ctrl+c", "ask_stop", priority=True, show=False),
        Binding("q", "ask_stop"),
    ]

    def __init__(self):
        super().__init__()
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.handle = RunHandle()
        self.pending: concurrent.futures.Future | None = None
        self.stop_reason: int | None = None  # 130, 143, 129
        self.refreshes = 0

    def compose(self):
        yield Static("starting", id="status")
        yield Static("", id="question")

    # signals: on the app's loop, which is the main thread's loop
    def on_mount(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            loop.add_signal_handler(sig, self.on_signal, sig)
        self.set_interval(0.1, self.drain)
        threading.Thread(
            target=self.startup_thread, name="startup", daemon=True
        ).start()

    def on_unmount(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            loop.remove_signal_handler(sig)
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(StartupStopped())

    def on_signal(self, sig):
        trace("ui: signal", signal.Signals(sig).name)
        self.request_stop(128 + sig)

    # the start-up thread
    def startup_thread(self):
        try:
            answers = startup_flow(self.ask_from_thread)
        except StartupStopped:
            trace("startup: stopped")
            return
        self.events.put(("startup_done", answers))
        th = threading.Thread(
            target=run_thread_main,
            args=(
                self.handle,
                self.events,
                lambda: self.events.put(("run_ended", None)),
            ),
            name="run",
            daemon=False,
        )
        self.handle.thread = th
        th.start()

    def ask_from_thread(self, question: str) -> str:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.events.put(("ask", (question, fut)))
        return fut.result()  # blocks the start-up thread only

    # the queue drain: the only place run/start-up data reaches widgets
    def drain(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self.apply(kind, data)
        except queue.Empty:
            pass

    def apply(self, kind, data):
        try:  # guard: one bad refresh must not kill the app
            if FAULT == "caught" and kind == "wave" and data == 3:
                raise ValueError("bad refresh (caught)")
            self.refreshes += 1
            if kind == "ask":
                question, fut = data
                self.pending = fut
                self.query_one("#question", Static).update(f"? {question} (press a)")
            elif kind == "startup_done":
                self.query_one("#question", Static).update("")
                self.query_one("#status", Static).update(f"run starting {data}")
            elif kind == "wave":
                self.query_one("#status", Static).update(f"wave {data}")
            elif kind == "run_ended":
                trace("ui: run ended, outcome", self.handle.outcome)
                code = (
                    self.stop_reason
                    if self.stop_reason is not None
                    else (0 if self.handle.outcome.startswith("finished") else 1)
                )
                self.exit(code, return_code=code)
        except Exception as e:  # noqa: BLE001
            trace("ui: guarded refresh failed:", repr(e))
        if FAULT == "uncaught" and kind == "wave" and data == 3:
            raise RuntimeError("bad refresh (uncaught)")

    def on_key(self, event):
        if event.key == "a" and self.pending is not None and not self.pending.done():
            self.pending.set_result("yes")
            self.pending = None

    def action_ask_stop(self):
        if isinstance(self.screen, Confirm):
            return

        def done(stop):
            if stop:
                self.request_stop(130)

        self.push_screen(Confirm(), done)

    def request_stop(self, code: int):
        if self.stop_reason is not None:
            return
        self.stop_reason = code
        self.query_one("#status", Static).update("Stopping…")
        if self.handle.thread is None:  # still in the start-up flow
            self.exit(code, return_code=code)
        else:
            threading.Thread(target=self.handle.cancel, daemon=True).start()


def main() -> int:
    import faulthandler

    global _fh
    _fh = open(os.path.join(HERE, "e4_stacks.txt"), "w")
    faulthandler.register(signal.SIGUSR1, file=_fh, all_threads=True)
    if os.path.exists(TRACE):
        os.remove(TRACE)
    safe = os.environ.get("E4_SAFE") == "1"
    if safe:
        # The driver gets its own dup of the terminal and tolerates EIO;
        # fds 1 and 2 point at the run log while the app runs.
        import e5_stderr
        from e5_stderr import TTYDriver

        e5_stderr.TTY_FD = os.dup(2)
        saved = (os.dup(1), os.dup(2))
        log_fd = os.open(
            os.path.join(HERE, "e4_runlog.txt"),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)
        TunerApp.driver_class_for_test = TTYDriver
        app = TunerApp()
        app.driver_class = TTYDriver
    else:
        app = TunerApp()
    a = time.monotonic()
    app.run()
    if safe:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
    trace(
        f"main: App.run() returned after {time.monotonic() - a:.2f}s return_code={app.return_code} exception={app._exception!r}"  # noqa: E501
    )
    th = app.handle.thread
    if th is not None and th.is_alive():
        # the UI died or quit while the run still goes on: stop it, then wait
        trace("main: run thread still alive after the UI ended; cancelling and joining")
        app.handle.cancel()
        th.join()
    code = app.return_code if app.return_code is not None else 1
    print("harness-tuner: closing block")
    print(
        f"  outcome: {app.handle.outcome}  exit: {code}  ui refreshes: {app.refreshes}"
    )
    trace("main: exiting with", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
