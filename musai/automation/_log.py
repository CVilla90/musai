"""Tiny colored logger with indentation — matches the style of the existing
sega_grade_uploader / moodle_suite tools (step / success / warn / error)."""

from __future__ import annotations


class Logger:
    COLORS = {
        "HEADER": "\033[95m", "INFO": "\033[94m", "SUCCESS": "\033[92m",
        "WARNING": "\033[93m", "ERROR": "\033[91m", "BOLD": "\033[1m", "END": "\033[0m",
    }

    def __init__(self) -> None:
        self.indent_level = 0

    def _print(self, message: str, color: str | None = None) -> None:
        indent = "  " * self.indent_level
        if color and color in self.COLORS:
            print(f"{self.COLORS[color]}{indent}{message}{self.COLORS['END']}")
        else:
            print(f"{indent}{message}")

    def header(self, message: str) -> None:
        self._print(f"\n{'=' * 60}", "HEADER")
        self._print(message, "HEADER")
        self._print("=" * 60, "HEADER")

    def info(self, message: str) -> None:
        self._print(f"ℹ {message}", "INFO")

    def success(self, message: str) -> None:
        self._print(f"✓ {message}", "SUCCESS")

    def warning(self, message: str) -> None:
        self._print(f"⚠ {message}", "WARNING")

    def error(self, message: str) -> None:
        self._print(f"✗ {message}", "ERROR")

    def step(self, message: str) -> None:
        self._print(f"→ {message}", "BOLD")

    def indent(self) -> None:
        self.indent_level += 1

    def dedent(self) -> None:
        self.indent_level = max(0, self.indent_level - 1)


logger = Logger()


def describe_exception(exc: BaseException) -> str:
    """A description of an exception that is never the empty string.

    ``str(NotImplementedError())`` is ``""``, so a handler written as ``f"ERROR: {e}"`` reports
    a real failure as "ERROR: " with nothing after it — which is precisely how a Playwright
    crash reached the cockpit on 2026-08-07 looking like nothing at all. Always keep the class
    name, so the worst case is still a name to search for.
    """
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
