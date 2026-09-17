from __future__ import annotations

from .parse import Parser


# Maps AST binary-operator characters to IR opcodes.
_BINOP_IR = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "%": "mod",
    "==": "eq",
    "!=": "ne",
    "<": "lt",
    ">": "gt",
    "<=": "le",
    ">=": "ge",
    "&&": "and",
    "||": "or",
}

# Compound-assignment ops -> underlying arithmetic opcode.
_ASSIGN_IR = {
    "+=": "add",
    "-=": "sub",
    "*=": "mul",
    "/=": "div",
    "%=": "mod",
}


class IRLowerer:
    """Lower a parsed .mcl AST into a line-per-operation IR.

    The IR stays close to the eventual Minecraft function shape: each
    instruction is a single atomic operation written to a destination
    variable, sub-expressions are broken into temporaries, and functions
    are labeled units that may return a value. There are deliberately **no**
    gotos / jump labels: control flow (``if``/``while``/``for``) stays
    structured. There are also no Minecraft concepts yet (no scoreboards,
    no ``$`` macros, no ``/return`` command) -- those belong to the later
    mcfunction emission pass; this layer works in pure, named variables.
    """

    def __init__(self) -> None:
        self._temp_counter = 0

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def lower(self, ast: dict) -> dict:
        funcs: list[dict] = []
        synthetic: list[dict] = []
        for node in ast.get("body", []):
            if node["type"] == "fn":
                funcs.append(self._lower_fn(node))
            else:
                synthetic.append(node)
        if synthetic:
            self._temp_counter = 0
            body: list[dict] = []
            for s in synthetic:
                self._lower_stmt(s, body)
            funcs.append(
                {
                    "op": "fn",
                    "name": "main",
                    "params": [],
                    "body": body,
                }
            )
        return {"type": "ir_program", "body": funcs}

    # ------------------------------------------------------------------ #
    # Temp / value helpers
    # ------------------------------------------------------------------ #
    def _fresh(self) -> str:
        name = f"_t{self._temp_counter}"
        self._temp_counter += 1
        return name

    @staticmethod
    def _lit(node: dict):
        """Convert an AST literal node into an IR value."""
        return {"type": "literal", "kind": node.get("kind"), "value": node["value"]}

    @staticmethod
    def _var(name: str):
        return {"type": "var", "name": name}

    @staticmethod
    def _value(node: dict):
        """Turn a value-bearing AST node (literal/identifier) into a Value."""
        if node["type"] == "literal":
            return IRLowerer._lit(node)
        if node["type"] == "identifier":
            return IRLowerer._var(node["name"])
        raise ValueError(f"Not a simple value: {node['type']}")

    def _emit(self, out: list[dict], op: str, **fields) -> dict:
        instr = {"op": op}
        instr.update(fields)
        out.append(instr)
        return instr

    # ------------------------------------------------------------------ #
    # Function / block lowering
    # ------------------------------------------------------------------ #
    def _lower_fn(self, node: dict) -> dict:
        self._temp_counter = 0
        body: list[dict] = []
        self._lower_block(node["body"], body)
        return {
            "op": "fn",
            "name": node["name"],
            "params": list(node["params"]),
            "body": body,
        }

    def _lower_block(self, node: dict, out: list[dict]) -> None:
        for stmt in node.get("statements", []):
            self._lower_stmt(stmt, out)

    def _lower_body(self, node: dict) -> list[dict]:
        """Lower either a block node or a single statement node into a list."""
        out: list[dict] = []
        if node is None:
            return out
        if node["type"] == "block":
            self._lower_block(node, out)
        else:
            self._lower_stmt(node, out)
        return out

    # ------------------------------------------------------------------ #
    # Statement lowering
    # ------------------------------------------------------------------ #
    def _lower_stmt(self, stmt: dict, out: list[dict]) -> None:
        kind = stmt["type"]

        if kind == "expr_stmt":
            self._lower_expr_stmt(stmt["expr"], out)
            return
        if kind == "return":
            value = self._lower_return_value(stmt.get("value"), out)
            self._emit(out, "return", value=value)
            return
        if kind == "if":
            out.append(self._lower_if(stmt))
            return
        if kind == "while":
            out.append(self._lower_while(stmt))
            return
        if kind == "for":
            out.append(self._lower_for(stmt))
            return
        if kind in ("break", "continue"):
            self._emit(out, kind)
            return
        # Bare blocks are valid statements.
        if kind == "block":
            self._lower_block(stmt, out)
            return

    def _lower_expr_stmt(self, expr: dict, out: list[dict]) -> None:
        et = expr["type"]
        if et == "assign":
            self._lower_assign_stmt(expr, out)
        elif et == "call":
            self._lower_call(expr, out, target="_")
        else:
            # Expression result is discarded; lower into a throwaway temp.
            self._lower_expr(expr, out)

    def _lower_assign_stmt(self, assign: dict, out: list[dict]) -> None:
        target = assign["target"]
        op = assign["op"]
        if op == "=" and target["type"] == "identifier":
            # Write the value directly into the destination variable.
            self._lower_expr(assign["value"], out, result_var=target["name"])
            return
        if op in ("+=", "-=", "*=", "/=", "%="):
            if target["type"] == "identifier":
                rv = self._lower_expr(assign["value"], out)
                self._emit(
                    out,
                    _ASSIGN_IR[op],
                    target=target["name"],
                    left=self._var(target["name"]),
                    right=rv,
                )
                return
        # Compound / member / index target.
        rv = self._lower_expr(assign["value"], out)
        self._store_value(target, rv, out)

    def _lower_call(self, call: dict, out: list[dict], target=None) -> dict:
        args = [self._lower_expr(a, out) for a in call["args"]]
        tgt = target if target is not None else self._fresh()
        self._emit(
            out,
            "call",
            target=tgt,
            callee=call["callee"]["name"],
            args=args,
        )
        return self._var(tgt)

    # ------------------------------------------------------------------ #
    # Expression lowering -> returns a Value (var or literal)
    # ------------------------------------------------------------------ #
    def _lower_expr(self, expr: dict, out: list[dict], result_var=None) -> dict:
        """Lower an expression, appending ops to ``out``.

        Returns a Value describing where the result lives.  When
        ``result_var`` is given the final operation writes directly into
        that variable instead of allocating a fresh temporary.
        """
        et = expr["type"]

        if et == "literal":
            v = self._lit(expr)
            if result_var is not None:
                self._emit(out, "set", target=result_var, value=v)
                return self._var(result_var)
            return v

        if et == "identifier":
            v = self._var(expr["name"])
            if result_var is not None:
                self._emit(out, "set", target=result_var, value=v)
                return self._var(result_var)
            return v

        if et == "binary":
            lv = self._lower_expr(expr["left"], out)
            rv = self._lower_expr(expr["right"], out)
            tgt = result_var if result_var is not None else self._fresh()
            irop = _BINOP_IR[expr["op"]]
            self._emit(out, irop, target=tgt, left=lv, right=rv)
            return self._var(tgt)

        if et == "unary":
            ov = self._lower_expr(expr["operand"], out)
            op = expr["op"]
            tgt = result_var if result_var is not None else self._fresh()
            if op == "-":
                self._emit(out, "neg", target=tgt, operand=ov)
            elif op == "+":
                self._emit(out, "set", target=tgt, value=ov)
            elif op == "!":
                self._emit(out, "not", target=tgt, operand=ov)
            return self._var(tgt)

        if et == "assign":
            # Assignment used as a value (e.g. chained assignment).
            if op_eq(expr["op"]) and target_is_ident(expr["target"]):
                tgt = expr["target"]["name"]
                self._lower_expr(expr["value"], out, result_var=tgt)
                if result_var is not None and result_var != tgt:
                    self._emit(out, "set", target=result_var, value=self._var(tgt))
                    return self._var(result_var)
                return self._var(tgt)
            rv = self._lower_expr(expr["value"], out)
            self._store_value(expr["target"], rv, out)
            if result_var is not None:
                self._emit(out, "set", target=result_var, value=rv)
                return self._var(result_var)
            return rv

        if et == "call":
            return self._lower_call(expr, out, target=result_var)

        if et == "member":
            obj = self._lower_expr(expr["object"], out)
            tgt = result_var if result_var is not None else self._fresh()
            self._emit(out, "get_member", target=tgt, obj=obj, name=expr["name"])
            return self._var(tgt)

        if et == "index":
            obj = self._lower_expr(expr["object"], out)
            idx = self._lower_expr(expr["index"], out)
            tgt = result_var if result_var is not None else self._fresh()
            self._emit(out, "get_index", target=tgt, obj=obj, index=idx)
            return self._var(tgt)

        raise ValueError(f"Cannot lower expression node: {et}")

    def _store_value(self, target: dict, value: dict, out: list[dict]) -> None:
        """Store ``value`` into an lvalue (identifier / member / index)."""
        if target["type"] == "identifier":
            self._emit(out, "set", target=target["name"], value=value)
        elif target["type"] == "member":
            obj = self._lower_expr(target["object"], out)
            self._emit(out, "set_member", obj=obj, name=target["name"], value=value)
        elif target["type"] == "index":
            obj = self._lower_expr(target["object"], out)
            idx = self._lower_expr(target["index"], out)
            self._emit(out, "set_index", obj=obj, index=idx, value=value)

    # ------------------------------------------------------------------ #
    # Value/condition helpers
    # ------------------------------------------------------------------ #
    def _lower_return_value(self, node: dict | None, out: list[dict]) -> dict | None:
        if node is None:
            return None
        return self._lower_expr(node, out)

    def _lower_condition(self, node: dict) -> tuple[list[dict], dict]:
        """Lower a condition expression -> (instrs, Value).

        ``instrs`` recomputes a boolean Value (1/0) into a fresh temp and is
        suitable for re-execution each loop iteration.
        """
        if node is None:
            return [], None
        ops: list[dict] = []
        val = self._lower_expr(node, ops)
        return ops, val

    # ------------------------------------------------------------------ #
    # Control flow lowering (structured: no gotos / jump labels)
    # ------------------------------------------------------------------ #
    def _lower_if(self, node: dict) -> dict:
        cond_ops, cond_val = self._lower_condition(node["condition"])
        then_body = self._lower_body(node["body"])
        else_body: list[dict] | None = None
        if node.get("else") is not None:
            else_body = self._lower_else(node["else"])
        return {
            "op": "if",
            "cond_ops": cond_ops,
            "cond": cond_val,
            "body": then_body,
            "else": else_body,
        }

    def _lower_else(self, node: dict) -> list[dict]:
        out: list[dict] = []
        if node["type"] == "block":
            self._lower_block(node, out)
        elif node["type"] == "elif":
            out.append(self._lower_if(node))
        else:
            self._lower_stmt(node, out)
        return out

    def _lower_while(self, node: dict) -> dict:
        cond_ops, cond_val = self._lower_condition(node["condition"])
        body = self._lower_body(node["body"])
        return {
            "op": "while",
            "cond_ops": cond_ops,
            "cond": cond_val,
            "body": body,
        }

    def _lower_for(self, node: dict) -> dict:
        init = self._lower_for_header(node.get("init"))
        cond_ops, cond_val = self._lower_condition(node.get("condition"))
        update = self._lower_for_header(node.get("update"))
        body = self._lower_body(node.get("body"))
        return {
            "op": "for",
            "init": init,
            "cond_ops": cond_ops,
            "cond": cond_val,
            "update": update,
            "body": body,
            "single": bool(node.get("single", False)),
        }

    def _lower_for_header(self, node: dict | None) -> list[dict]:
        out: list[dict] = []
        if node is None:
            return out
        if node["type"] == "assign":
            self._lower_assign_stmt(node, out)
        elif node["type"] == "call":
            self._lower_call(node, out, target="_")
        else:
            self._lower_expr(node, out)
        return out


def op_eq(op: str) -> bool:
    return op == "="


def target_is_ident(target: dict) -> bool:
    return target["type"] == "identifier"


def lower(ast: dict) -> dict:
    """Lower a parsed .mcl AST into a line-per-operation IR."""
    return IRLowerer().lower(ast)


# ---------------------------------------------------------------------- #
# Text serializer (one IR operation per line, indented for nesting)
# ---------------------------------------------------------------------- #
def _fmt_value(v: dict) -> str:
    if v["type"] == "literal":
        val = v["value"]
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, str):
            return f'"{val}"'
        return str(val)
    if v["type"] == "var":
        return v["name"]
    raise ValueError("bad value")


def to_text(ir: dict) -> str:
    lines: list[str] = []

    def push(depth: int, text: str) -> None:
        lines.append("  " * depth + text)

    def emit_list(seq: list[dict], depth: int) -> None:
        for instr in seq:
            emit_one(instr, depth)

    def emit_one(instr: dict, depth: int) -> None:
        op = instr["op"]
        if op == "fn":
            params = ", ".join(instr["params"])
            push(depth, f"fn {instr['name']}({params})")
            emit_list(instr["body"], depth + 1)
            push(depth, "end")
        elif op == "set":
            push(depth, f"set {instr['target']} = {_fmt_value(instr['value'])}")
        elif op in ("add", "sub", "mul", "div", "mod",
                    "eq", "ne", "lt", "gt", "le", "ge", "and", "or"):
            push(depth, f"{op} {instr['target']} {_fmt_value(instr['left'])} {_fmt_value(instr['right'])}")
        elif op in ("neg", "not"):
            push(depth, f"{op} {instr['target']} {_fmt_value(instr['operand'])}")
        elif op == "get_member":
            push(depth, f"get_member {instr['target']} {_fmt_value(instr['obj'])}.{instr['name']}")
        elif op == "get_index":
            push(depth, f"get_index {instr['target']} {_fmt_value(instr['obj'])}[{_fmt_value(instr['index'])}]")
        elif op == "set_member":
            push(depth, f"set_member {_fmt_value(instr['obj'])}.{instr['name']} = {_fmt_value(instr['value'])}")
        elif op == "set_index":
            push(depth, f"set_index {_fmt_value(instr['obj'])}[{_fmt_value(instr['index'])}] = {_fmt_value(instr['value'])}")
        elif op == "call":
            tgt = instr["target"]
            tgt_part = "" if tgt == "_" else f"{tgt} = "
            args = " ".join(_fmt_value(a) for a in instr["args"])
            push(depth, f"call {tgt_part}{instr['callee']}({args})")
        elif op == "print":
            push(depth, f"print {_fmt_value(instr['value'])}")
        elif op == "return":
            if instr.get("value") is None:
                push(depth, "return")
            else:
                push(depth, f"return {_fmt_value(instr['value'])}")
        elif op in ("break", "continue"):
            push(depth, op)
        elif op == "block":
            push(depth, "block")
            emit_list(instr["body"], depth + 1)
            push(depth, "end")
        elif op == "if":
            push(depth, f"begin_if {_fmt_cond(instr['cond'])}")
            emit_list(instr["cond_ops"], depth + 1)
            emit_list(instr["body"], depth + 1)
            if instr.get("else"):
                push(depth, "else")
                emit_list(instr["else"], depth + 1)
            push(depth, "end_if")
        elif op == "while":
            push(depth, f"begin_while {_fmt_cond(instr['cond'])}")
            emit_list(instr["cond_ops"], depth + 1)
            emit_list(instr["body"], depth + 1)
            push(depth, "end_while")
        elif op == "for":
            push(depth, f"begin_for {_fmt_cond(instr.get('cond'))}")
            emit_list(instr["init"], depth + 1)
            emit_list(instr["cond_ops"], depth + 1)
            emit_list(instr["body"], depth + 1)
            emit_list(instr["update"], depth + 1)
            push(depth, "end_for")
        else:
            push(depth, f"# {op}")

    def _fmt_cond(v: dict | None) -> str:
        return "true" if v is None else _fmt_value(v)

    emit_list(ir["body"], 0)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "example.mcl"
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    ast = Parser(source).parse()
    ir = lower(ast)
    if "--json" in sys.argv:
        import json
        print(json.dumps(ir, indent=2))
    else:
        print(to_text(ir))
