from __future__ import annotations

from contextlib import contextmanager
from types import NotImplementedType
from typing import Any, Callable, NoReturn

from colorama import Back, Fore, Style


class TextStream:
    """Provide cursor-based text consumption with source-location metadata."""

    __slots__ = (
        "_text",
        "_pos",
        "_debug",
        "_range_start",
    )

    def __init__(self, text: str, debug: bool = True) -> None:
        self._text = text
        self._pos = 0
        self._debug = debug
        self._range_start: int | None = None

    @property
    def pos(self) -> int:
        """Absolute position in string."""
        return self._pos

    @pos.setter
    def pos(self, pos: int) -> None:
        """Move the cursor to an absolute source position."""
        if not 0 <= pos <= len(self._text):
            raise ValueError("pos out of range.")
        self._pos = pos

    @property
    def full(self) -> str:
        """Full text."""
        return self._text

    @full.setter
    def full(self, full: str) -> None:
        """Replace the stream text while preserving the cursor position."""
        if self._pos > len(full):
            raise ValueError("Stream position is outside new full text.")
        self._text = full

    @property
    def left(self) -> str:
        """Return the consumed prefix of the source text."""
        return self._text[: self._pos]

    @property
    def right(self) -> str:
        """Return the unconsumed suffix of the source text."""
        return self._text[self._pos :]

    @property
    def remaining(self) -> str:
        """Return the unconsumed suffix of the source text."""
        return self._text[self._pos :]

    @property
    def eof(self) -> bool:
        """Return whether the cursor has reached the end of input."""
        return self._pos >= len(self._text)

    def seek(self, n: int) -> None:
        """Set the absolute cursor position."""
        self.pos = n

    def peek(self, n: int = 1) -> str:
        """Return up to n upcoming characters without consuming them."""
        return self._text[self._pos : self._pos + n]

    def match(self, text: str) -> bool:
        """Return whether text matches at the current cursor."""
        return self._text.startswith(text, self._pos)

    def consume(self, n: int = 1) -> str:
        """Consume and return up to n characters."""
        start = self._pos
        end = min(start + n, len(self._text))
        self._pos = end
        return self._text[start:end]

    def consume_text(self, text: str) -> bool:
        """Consume text when it matches at the current cursor."""
        if self._text.startswith(text, self._pos):
            self._pos += len(text)
            return True
        return False

    def consume_until(self, text: str) -> str:
        """Consume and return text preceding the next occurrence."""
        start = self._pos
        end = self._text.find(text, start)

        if end == -1:
            self._pos = len(self._text)
        else:
            self._pos = end

        return self._text[start:self._pos]

    def consume_while(self, predicate: Callable[[str], bool]) -> str:
        """Consume while predicate returns True."""
        start = self._pos
        pos = start
        text = self._text
        length = len(text)

        while pos < length and predicate(text[pos]):
            pos += 1

        self._pos = pos
        return text[start:pos]

    @staticmethod
    def _is_word(c: str) -> bool:
        return c == "_" or c.isalnum()

    @staticmethod
    def _is_space(c: str) -> bool:
        return c.isspace()

    @staticmethod
    def _is_space_no_nl(c: str) -> bool:
        return c.isspace() and c != "\n"

    def consume_word(self) -> str:
        """Consume and return an alphanumeric or underscore identifier."""
        return self.consume_while(self._is_word)

    def consume_whitespace(self, newline: bool = True) -> str:
        """Consume whitespace."""
        return self.consume_while(
            self._is_space if newline else self._is_space_no_nl
        )

    @property
    def line(self) -> int:
        """Return the current one-based line number."""
        return self._text.count("\n", 0, self._pos) + 1

    @property
    def column(self) -> int:
        """Return the current one-based column number."""
        return self._pos - self._text.rfind("\n", 0, self._pos)

    @property
    def current_line(self) -> str:
        """Return the full source line containing the cursor."""
        start = self._text.rfind("\n", 0, self._pos) + 1
        end = self._text.find("\n", self._pos)
        if end == -1:
            end = len(self._text)
        return self._text[start:end]

    @contextmanager
    def range(self):
        """Context manager that records the start position."""
        self._range_start = self._pos
        try:
            yield
        finally:
            self._range_start = None

    def emit(self, value: dict[str, Any]) -> dict[str, Any]:
        """Return a node augmented with current source metadata."""
        if not self._debug:
            return value

        node = value | {
            "line": self.line,
            "column": self.column,
            "error": self.current_line,
        }

        if self._range_start is not None:
            node["range_start"] = self._range_start
            node["range_end"] = self._pos

        return node

    def error(
        self,
        error: str,
        text: str,
        highlight: str | None = None,
    ) -> NoReturn:
        """Print a formatted syntax error and terminate parsing."""
        line = self.current_line
        print(line)

        if highlight is None:
            print(" " * (self.column - 1) + "^")
        else:
            idx = line.find(highlight)
            if idx == -1:
                idx = self.column - 1
            print(" " * idx + "^" * len(highlight))

        print(f"{Fore.RED}{Style.BRIGHT}{Back.BLACK}{error}: {text}", Style.RESET_ALL)
        print(f"At line {self.line}, col {self.column}")
        raise SystemExit(-1)

    def expect(self, text: str) -> None:
        """Require text at the cursor."""
        if not self.consume_text(text):
            self.error("SyntaxError", f"Expected '{text}'")

    def __str__(self) -> str:
        return self._text

    def __bool__(self) -> bool:
        return self._pos < len(self._text)

    def __contains__(self, item: str) -> bool:
        return item in self._text

    def __iadd__(self, other: str) -> TextStream:
        self._text += other
        return self

    def __eq__(self, other: object) -> bool | NotImplementedType:
        if isinstance(other, TextStream):
            return self._text == other._text
        if isinstance(other, str):
            return self._text == other
        return NotImplemented

    def __repr__(self) -> str:
        return (
            f"TextStream(pos={self._pos}, "
            f"left={self.left!r}, "
            f"right={self.right!r})"
        )


