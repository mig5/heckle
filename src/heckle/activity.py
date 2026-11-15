"""A live status line for long-running steps.

On a terminal, a spinner line is redrawn in place with the step's current progress
and elapsed time. Anywhere else (CI logs, a pipe), a plain "still working" line is
printed at a fixed interval instead, so a long step never looks hung.

The status text comes from a describe() callback polled by the drawing thread, so
callers only keep their own counters up to date. It must be cheap and must never
include provider output, which can contain confidential values.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from typing import Callable, Iterator, TextIO

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_clock(epoch: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epoch))


class Activity:
    """Permanent progress messages plus one optional live status line."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        note: Callable[[str], None] | None = None,
        live: bool | None = None,
        interval: float = 0.1,
        heartbeat: float = 60.0,
    ) -> None:
        # With no stream, the status line is disabled and notes go to `note`.
        self.stream = stream
        self._note = note
        self.enabled = stream is not None
        self.interactive = (
            live if live is not None else bool(stream is not None and _isatty(stream))
        )
        self.interval = interval
        self.heartbeat = heartbeat
        self._lock = threading.Lock()
        self._label: str | None = None
        self._describe: Callable[[], str] = str
        self._started = 0.0
        self._shown = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def note(self, message: str) -> None:
        """Print a permanent line, keeping any live status line below it."""
        if not self.enabled:
            if self._note:
                self._note(message)
            return
        with self._lock:
            self._clear()
            print(message, file=self.stream, flush=True)
            self._draw()

    @contextlib.contextmanager
    def running(self, label: str, describe: Callable[[], str] = str) -> Iterator[None]:
        """Show `label` and describe() live until the block finishes."""
        if not self.enabled:
            yield
            return
        with self._lock:
            self._label, self._describe = label, describe
            self._started = time.monotonic()
            self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="heckle-activity", daemon=True)
        self._thread.start()
        try:
            yield
        finally:
            self._stop.set()
            self._thread.join()
            with self._lock:
                self._clear()
                self._label = None

    def _status(self) -> str:
        try:
            detail = self._describe()
        except Exception:  # A display problem must never break the work itself.
            detail = ""
        elapsed = format_duration(time.monotonic() - self._started)
        return f"{self._label}" + (f": {detail}" if detail else "") + f" · {elapsed}"

    def _loop(self) -> None:
        frame = 0
        last_heartbeat = time.monotonic()
        tick = self.interval if self.interactive else min(1.0, self.heartbeat)
        while not self._stop.wait(tick):
            with self._lock:
                if self.interactive:
                    frame = (frame + 1) % len(SPINNER)
                    self._draw(SPINNER[frame])
                elif time.monotonic() - last_heartbeat >= self.heartbeat:
                    last_heartbeat = time.monotonic()
                    print(f"... still working. {self._status()}", file=self.stream, flush=True)

    def _draw(self, glyph: str = SPINNER[0]) -> None:
        if not (self.interactive and self._label):
            return
        line = f"{glyph} {self._status()}"
        width = _columns(self.stream)
        if len(line) > width - 1:
            line = line[: max(0, width - 2)] + "…"
        self.stream.write("\r\x1b[2K" + line)
        self.stream.flush()
        self._shown = True

    def _clear(self) -> None:
        if self._shown:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
            self._shown = False


def _isatty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _columns(stream: TextIO) -> int:
    import shutil

    try:
        return max(20, shutil.get_terminal_size(fallback=(100, 24)).columns)
    except (OSError, ValueError):
        return 100


def default_activity() -> Activity:
    return Activity(sys.stderr)
