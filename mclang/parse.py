from __future__ import annotations

from .stream import TextStream


# Binary operator precedence. Higher number = tighter binding.
_PRECEDENCE: dict[str, int] = {
    "||": 1,
    "&&": 2,
    "==": 3,
    "!=": 3,
    "<": 4,
    ">": 4,
    "<=": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
}

_STMT_KEYWORDS = {
    "fn",
    "return",
    "if",
    "elif",
    "else",
    "for",
    "while",
    "break",
    "continue",
}


class Parser:
    """Recursive-descent parser for the .mcl language.

    The grammar is extrapolated from ``example.mcl`` and extended into a full
    small C-style scripting language designed to compile into Minecraft
    ``.mcfunction`` commands. Parsing uses ``TextStream`` exclusively for all
    text matching (no regular expressions).
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.stream = TextStream(source, debug=False)

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def parse(self) -> dict:
        self._blank()
        body: list[dict] = []
        self.stream.range()
        while not self.stream.eof:
            stmt = self._parse_stmt()
            if stmt is not None:
                body.append(stmt)
            self._blank()
        return self.stream.emit({"type": "program", "body": body})

    # ------------------------------------------------------------------ #
    # Low-level helpers (all text matching goes through TextStream)
    # ------------------------------------------------------------------ #
    def _ws(self, nl: bool = True) -> str:
        return self.stream.consume_whitespace(newline=nl)

    def _blank(self) -> None:
        """Consume whitespace, newlines, and comments."""
        while True:
            before = self.stream.pos
            self._ws(True)
            if self._try_comment():
                continue
            if self.stream.pos == before:
                break

    def _peek(self, n: int = 1) -> str:
        return self.stream.peek(n)

    def _match(self, text: str) -> bool:
        return self.stream.match(text)

    def _expect(self, text: str) -> None:
        self._ws(True)
        if not self.stream.consume_text(text):
            self.stream.error(
                "SyntaxError", f"Expected '{text}'", highlight=text
            )

    def _consume(self, text: str) -> None:
        if not self.stream.consume_text(text):
            self.stream.error("SyntaxError", f"Expected '{text}'")

    def _try_comment(self) -> bool:
        if self._match("//"):
            self.stream.consume()
            self.stream.consume_until("\n")
            return True
        if self._match("/*"):
            self.stream.consume(2)
            self.stream.consume_until("*/")
            if self._match("*/"):
                self.stream.consume(2)
            return True
        return False

    def _peek_word(self) -> str:
        start = self.stream.pos
        self._ws(True)
        word = self.stream.consume_word()
        self.stream.pos = start
        return word

    def _try_word(self, word: str) -> bool:
        start = self.stream.pos
        self._ws(True)
        tok = self.stream.consume_word()
        if tok == word:
            return True
        self.stream.pos = start
        return False

    def _parse_ident(self) -> str:
        self._ws(True)
        word = self.stream.consume_word()
        if not word:
            self.stream.error("SyntaxError", "Expected an identifier")
        if word in _STMT_KEYWORDS:
            self.stream.error(
                "SyntaxError", f"Keyword '{word}' used as an identifier",
                highlight=word,
            )
        return word

    def _parse_string(self, quote: str) -> dict:
        self.stream.range()
        self.stream.consume(1)  # opening quote
        parts: list[str] = []
        while not self.stream.eof and not self._match(quote):
            ch = self.stream.consume()
            if ch == "\\" and not self.stream.eof:
                nxt = self.stream.peek()
                self.stream.consume()
                esc = {
                    "n": "\n",
                    "t": "\t",
                    "r": "\r",
                    '"': '"',
                    "'": "'",
                    "\\": "\\",
                }.get(nxt, nxt)
                parts.append(esc)
            else:
                parts.append(ch)
        self._expect(quote)
        value = "".join(parts)
        return self.stream.emit(
            {"type": "literal", "kind": "string", "value": value}
        )

    def _parse_number(self) -> dict:
        self.stream.range()
        digits = self.stream.consume_while(lambda c: c.isdigit())
        if self._match("."):
            self.stream.consume()
            frac = self.stream.consume_while(lambda c: c.isdigit())
            raw = digits + "." + frac
            value: int | float = float(raw)
            kind = "float"
        else:
            raw = digits
            value = int(raw, 10) if raw else 0
            kind = "int"
        return self.stream.emit(
            {"type": "literal", "kind": kind, "value": value}
        )

    # ------------------------------------------------------------------ #
    # Statement parsing
    # ------------------------------------------------------------------ #
    def _parse_stmt(self) -> dict | None:
        self._blank()
        if self.stream.eof:
            return None
        if self._match("{"):
            return self._parse_block()

        word = self._peek_word()
        if not word or word not in _STMT_KEYWORDS:
            # Expression statement (assignments, calls, etc.)
            expr = self._parse_expr()
            self._end_stmt()
            return self.stream.emit({"type": "expr_stmt", "expr": expr})

        # Keyword-led statement: consume the keyword once and dispatch.
        self._ws(True)
        kw = self.stream.consume_word()
        if kw == "fn":
            return self._parse_fn()
        if kw == "return":
            return self._parse_return()
        if kw == "if":
            return self._parse_if()
        if kw == "for":
            return self._parse_for()
        if kw == "while":
            return self._parse_while()
        if kw == "break":
            self._end_stmt()
            return self.stream.emit({"type": "break"})
        if kw == "continue":
            self._end_stmt()
            return self.stream.emit({"type": "continue"})
        self.stream.error("SyntaxError", f"Unexpected keyword '{kw}'")

    def _end_stmt(self) -> None:
        self._ws(True)
        while self._match(";") or self._match("\n"):
            self.stream.consume()
            self._ws(True)

    def _parse_block(self) -> dict:
        self.stream.range()
        self._expect("{")
        stmts: list[dict] = []
        self._blank()
        while not self.stream.eof and not self._match("}"):
            stmt = self._parse_stmt()
            if stmt is not None:
                stmts.append(stmt)
            self._blank()
        self._expect("}")
        return self.stream.emit({"type": "block", "statements": stmts})

    def _parse_fn(self) -> dict:
        self.stream.range()
        name = self._parse_ident()
        self._expect("(")
        params: list[str] = []
        self._ws(True)
        if not self._match(")"):
            while True:
                params.append(self._parse_ident())
                self._ws(True)
                if self._match(","):
                    self.stream.consume()
                    self._ws(True)
                    continue
                break
        self._expect(")")
        body = self._parse_block()
        return self.stream.emit(
            {"type": "fn", "name": name, "params": params, "body": body}
        )

    def _parse_return(self) -> dict:
        self.stream.range()
        self._ws(True)
        value: dict | None = None
        if not self.stream.eof and not self._match("}"):
            if not self._match("\n"):
                value = self._parse_expr()
        self._end_stmt()
        return self.stream.emit({"type": "return", "value": value})

    def _parse_if(self) -> dict:
        self.stream.range()
        cond = self._parse_paren_expr()
        then = self._parse_block_or_brace()
        else_node = self._parse_if_else()
        return self.stream.emit(
            {"type": "if", "condition": cond, "body": then, "else": else_node}
        )

    def _parse_if_else(self) -> dict | None:
        self._blank()
        if self._try_word("elif"):
            cond = self._parse_paren_expr()
            then = self._parse_block_or_brace()
            node = self.stream.emit(
                {"type": "elif", "condition": cond, "body": then, "else": None}
            )
            node["else"] = self._parse_if_else()
            return node
        if self._try_word("else"):
            self._blank()
            if self._match("{"):
                return self._parse_block()
            return self._parse_stmt()
        return None

    def _parse_while(self) -> dict:
        self.stream.range()
        cond = self._parse_paren_expr()
        body = self._parse_block_or_brace()
        return self.stream.emit(
            {"type": "while", "condition": cond, "body": body}
        )

    def _parse_for(self) -> dict:
        self.stream.range()
        self._expect("(")
        self._ws(True)

        emit = self.stream.emit

        # Case A: empty init  for (; cond ; update)
        if self._match(";"):
            self.stream.consume()
            cond = self._parse_expr_or_empty()
            self._expect(";")
            self._ws(True)
            update = self._parse_expr_or_empty()
            self._expect(")")
            return emit(
                {
                    "type": "for",
                    "init": None,
                    "condition": cond,
                    "update": update,
                    "body": self._parse_block_or_brace(),
                }
            )

        # Case B: expression (or empty) init, then either ')' (single cond)
        # or ';' (traditional 3-statement form).
        if self._match(")"):
            self.stream.consume()
            return emit(
                {
                    "type": "for",
                    "init": None,
                    "condition": None,
                    "update": None,
                    "body": self._parse_block_or_brace(),
                }
            )
        init = self._parse_expr()
        self._ws(True)
        if self._match(")"):
            self.stream.consume()
            return emit(
                {
                    "type": "for",
                    "init": None,
                    "condition": init,
                    "update": None,
                    "body": self._parse_block_or_brace(),
                    "single": True,
                }
            )
        self._expect(";")
        self._ws(True)
        cond = self._parse_expr_or_empty()
        self._expect(";")
        self._ws(True)
        update = self._parse_expr_or_empty()
        self._expect(")")
        return emit(
            {
                "type": "for",
                "init": init,
                "condition": cond,
                "update": update,
                "body": self._parse_block_or_brace(),
            }
        )

    def _parse_paren_expr(self) -> dict:
        self._expect("(")
        node = self._parse_expr(nl=True)
        self._expect(")")
        return node

    def _parse_block_or_brace(self) -> dict:
        self._blank()
        if self._match("{"):
            return self._parse_block()
        return self._parse_stmt()

    def _parse_expr_or_empty(self) -> dict | None:
        self._ws(True)
        if self._match(";") or self._match(")"):
            return None
        return self._parse_expr(nl=True)

    # ------------------------------------------------------------------ #
    # Expression parsing
    # ------------------------------------------------------------------ #
    def _parse_expr(self, nl: bool = False) -> dict:
        left = self._parse_binop(0, nl)
        self._ws(nl)
        op = self._peek_assign_op()
        if op:
            self._consume_op(op)
            right = self._parse_expr(nl)
            return self.stream.emit(
                {"type": "assign", "op": op, "target": left, "value": right}
            )
        return left

    def _parse_binop(self, min_prec: int, nl: bool) -> dict:
        left = self._parse_unary(nl)
        while True:
            self._ws(nl)
            op = self._peek_binop()
            if op is None:
                break
            prec = _PRECEDENCE.get(op)
            if prec is None or prec < min_prec:
                break
            self._consume_op(op)
            right = self._parse_binop(prec + 1, nl)
            left = self.stream.emit(
                {"type": "binary", "op": op, "left": left, "right": right}
            )
        return left

    def _parse_unary(self, nl: bool) -> dict:
        self._ws(nl)
        for op in ("!", "-", "+"):
            if self._match(op):
                self.stream.consume(len(op))
                operand = self._parse_unary(nl)
                return self.stream.emit(
                    {"type": "unary", "op": op, "operand": operand}
                )
        return self._parse_postfix(nl)

    def _parse_postfix(self, nl: bool) -> dict:
        node = self._parse_primary(nl)
        while True:
            self._ws(nl)
            if self._match("("):
                self.stream.consume()
                args = self._parse_call_args()
                node = self.stream.emit(
                    {"type": "call", "callee": node, "args": args}
                )
            elif self._match("."):
                self.stream.consume()
                name = self._parse_ident()
                node = self.stream.emit(
                    {"type": "member", "object": node, "name": name}
                )
            elif self._match("["):
                self.stream.consume()
                idx = self._parse_expr(nl=True)
                self._ws(True)
                self._expect("]")
                node = self.stream.emit(
                    {"type": "index", "object": node, "index": idx}
                )
            else:
                break
        return node

    def _parse_call_args(self) -> list[dict]:
        self._ws(True)
        args: list[dict] = []
        if self._match(")"):
            self.stream.consume()
            return args
        while True:
            args.append(self._parse_expr(nl=True))
            self._ws(True)
            if self._match(","):
                self.stream.consume()
                self._ws(True)
                continue
            break
        self._expect(")")
        return args

    def _parse_primary(self, nl: bool) -> dict:
        self._ws(nl)
        if self._match("("):
            self.stream.consume()
            node = self._parse_expr(nl=True)
            self._ws(True)
            self._expect(")")
            return node
        if self._match('"'):
            return self._parse_string('"')
        if self._match("'"):
            return self._parse_string("'")
        c = self.stream.peek()
        if c and c.isdigit():
            return self._parse_number()
        if c == "." and self.stream.peek(2)[1:2].isdigit():
            return self._parse_number()
        word = self.stream.consume_word()
        if not word:
            self.stream.error("SyntaxError", "Expected an expression")
        if word == "true":
            return self.stream.emit(
                {"type": "literal", "kind": "boolean", "value": True}
            )
        if word == "false":
            return self.stream.emit(
                {"type": "literal", "kind": "boolean", "value": False}
            )
        return self.stream.emit({"type": "identifier", "name": word})

    # ------------------------------------------------------------------ #
    # Operator peeking / consuming
    # ------------------------------------------------------------------ #
    def _peek_binop(self) -> str | None:
        for op in ("==", "!=", "<=", ">=", "&&", "||"):
            if self._match(op):
                return op
        c = self.stream.peek()
        if c in "+-*/%<>":
            return c
        return None

    def _peek_assign_op(self) -> str | None:
        for op in ("+=", "-=", "*=", "/=", "%="):
            if self._match(op):
                return op
        if self._match("="):
            return "="
        return None

    def _consume_op(self, op: str) -> None:
        if not self.stream.consume_text(op):
            self.stream.error("SyntaxError", f"Expected operator '{op}'")


def parse(source: str) -> dict:
    """Parse .mcl source text into a consistent AST dictionary."""
    return Parser(source).parse()


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "example.mcl"
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    print(json.dumps(parse(text), indent=2))
