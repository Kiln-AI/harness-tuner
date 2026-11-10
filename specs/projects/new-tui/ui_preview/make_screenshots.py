"""Save an SVG screenshot of every preview state into screenshots/.

Run it from the repository root (README.md has the same command):

    uv run --with textual==8.2.8 python \
        specs/projects/new-tui/ui_preview/make_screenshots.py

The app runs headless with `App.run_test`, with the spinner frame and the
input cursor fixed, so the files only change when the design does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from preview_app import PreviewApp
from textual.widgets import Input

OUT = Path(__file__).parent / "screenshots"
WIDE = (210, 62)
NARROW = (120, 40)

# (file name, state, theme, size, keys to press, SVG title). The key "end"
# scrolls the run screen to its bottom.
SHOTS = (
    ("run-starting-dark-210x62", "starting", "kiln-dark", WIDE, (), "Run · Starting"),
    ("run-planning-dark-210x62", "planning", "kiln-dark", WIDE, (), "Run · Planning"),
    ("run-running-dark-210x62", "running", "kiln-dark", WIDE, (), "Run · Running"),
    ("run-gate-dark-210x62", "gate", "kiln-dark", WIDE, (), "Run · Promotion gate"),
    ("run-done-dark-210x62", "done", "kiln-dark", WIDE, (), "Run · Done"),
    (
        "run-running-last-wave-dark-210x62",
        "running",
        "kiln-dark",
        WIDE,
        ("w",),
        "Run · Running, last wave",
    ),
    (
        "run-stop-dialog-dark-210x62",
        "running",
        "kiln-dark",
        WIDE,
        ("q",),
        "Run · Stop dialog",
    ),
    ("card-gallery-dark-210x62", "gallery", "kiln-dark", WIDE, (), "Card gallery"),
    (
        "startup-welcome-dark-210x62",
        "welcome",
        "kiln-dark",
        WIDE,
        (),
        "Start-up · Busy",
    ),
    (
        "startup-picker-dark-210x62",
        "picker",
        "kiln-dark",
        WIDE,
        (),
        "Start-up · Picker",
    ),
    (
        "startup-problem-dark-210x62",
        "problem",
        "kiln-dark",
        WIDE,
        (),
        "Start-up · Problem",
    ),
    (
        "startup-text-answer-dark-210x62",
        "text",
        "kiln-dark",
        WIDE,
        (),
        "Start-up · Text answer",
    ),
    (
        "startup-summary-dark-210x62",
        "summary",
        "kiln-dark",
        WIDE,
        (),
        "Start-up · Summary",
    ),
    ("run-running-light-210x62", "running", "kiln-light", WIDE, (), "Run · Running"),
    (
        "startup-picker-light-210x62",
        "picker",
        "kiln-light",
        WIDE,
        (),
        "Start-up · Picker",
    ),
    (
        "run-running-dark-120x40",
        "running",
        "kiln-dark",
        NARROW,
        (),
        "Run · Running, stacked",
    ),
    (
        "run-running-bottom-dark-120x40",
        "running",
        "kiln-dark",
        NARROW,
        ("end",),
        "Run · Running, stacked, scrolled to the bottom",
    ),
)


async def shoot(
    name: str,
    state: str,
    theme: str,
    size: tuple[int, int],
    keys: tuple[str, ...],
    title: str,
) -> Path:
    app = PreviewApp(start=state, theme=theme, animate=False)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for key in keys:
            if key == "end":
                await pilot.pause()
                app.screen.query_one("#body").scroll_end(animate=False)
            else:
                await pilot.press(key)
            await pilot.pause()
        for answer in app.screen.query(Input):
            answer.cursor_blink = False
        # Let the layout settle: the grids pick their column counts on the
        # first resize, and the log renders once its width is known.
        for _ in range(3):
            await pilot.pause()
        svg = app.export_screenshot(title=title)
    path = OUT / f"{name}.svg"
    path.write_text(svg, encoding="utf-8")
    return path


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    for shot in SHOTS:
        print(await shoot(*shot))


if __name__ == "__main__":
    asyncio.run(main())
