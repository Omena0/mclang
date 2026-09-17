from __future__ import annotations

"""Compile a lowered .mcl IR program into a directory of ``.mcfunction`` files.

Target: Minecraft Java 1.21.4.  Only ``.mcfunction`` commands available in that
era are emitted -- no assumptions about newer syntax.

``/return`` semantics (1.21.4)
-----------------------------
``/return`` supports only three forms: ``return fail``, ``return <int>``, and
``return run <command>``.  There is **no** ``return score`` command, so returning
a dynamic scoreboard value requires the ``_ret`` slot convention: every
value-returning user function copies its result into ``_ret`` on the global
``_var`` objective, then ends with ``return 0`` (a constant int stop signal).
The caller invokes the function with a plain ``function <name>`` and reads
``_ret`` afterward -- we do *not* use ``execute store result ... run function``,
which is fragile for recursion.

Variable model
--------------
Every function gets its own scoreboard objective ``_var_<fn>`` (the function
name lower-cased and sanitised) for its locals, parameters and temporaries.
*All* objectives (the global ``_var`` plus one ``_var_<fn>`` per user function)
are created once in ``_init.mcfunction``, so user functions never emit
``scoreboard objectives add`` and can be called repeatedly without errors.

The global ``_var`` objective is reserved for *call plumbing*: argument slots
``_arg0`` .. ``_argN`` and the return slot ``_ret``.

There are **no** gotos.  ``if``/``while``/``for`` become ``execute if/unless``
guards over one-function-per-block files; loops are expressed with
self-recursive control functions (Minecraft has no jump opcodes).

``for (expr)`` single-argument form is a *counted* loop: a private counter is
initialised to ``expr`` and decremented once per iteration, so it runs exactly
that many times rather than looping forever.

The translation is driven by small dispatch tables / per-op handlers so that
new IR opcodes can be added by dropping in another ``_op_<name>`` method.
"""

import json
import os
import sys

from .ir import lower
from .parse import Parser


# --------------------------------------------------------------------------- #
# Static translation tables (extension points)
# --------------------------------------------------------------------------- #
# IR arithmetic opcode -> mcfunction `operation` symbol.
_ARITH_SYMBOL = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}

# Arithmetic ops that can fold a literal right-hand operand directly.
_ARITH_LITERAL_CMD = {"add": "add", "sub": "remove"}

# IR comparison opcode -> (execute-mode, score-operator) for var-vs-var.
_CMP_TWO = {
    "eq": ("if", "="),
    "ne": ("unless", "="),
    "lt": ("if", "<"),
    "gt": ("if", ">"),
    "le": ("unless", ">"),
    "ge": ("unless", "<"),
}

# Comparison opcode -> (execute-mode, range-builder) for `var op literal`.
# range-builder(v) yields a `matches` range meaning `var <op> v` is true.
_CMP_LIT = {
    "eq": ("if", lambda v: f"{v}"),
    "ne": ("unless", lambda v: f"{v}"),
    "lt": ("if", lambda v: f"..{v - 1}"),
    "gt": ("if", lambda v: f"{v + 1}.."),
    "le": ("if", lambda v: f"..{v}"),
    "ge": ("if", lambda v: f"{v}.."),
}

# Literal-on-the-left comparison opcodes flip to the equivalent `var op lit`.
_CMP_REVERSE = {
    "eq": "eq", "ne": "ne", "lt": "gt", "gt": "lt", "le": "ge", "ge": "le",
}

_BUILTINS = {"print"}

# Reserved global variable names living on the ``_var`` objective.
_ARG_PREFIX = "_arg"
_RET = "_ret"


def _ARG(i: int) -> str:
    """Return the name of the i-th call-argument slot on ``_var``."""
    return f"{_ARG_PREFIX}{i}"


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #
class MCCompiler:
    def __init__(self) -> None:
        self.files: dict[str, list[str]] = {}
        self._counter = 0
        self._func = ""            # current owning function (sanitised, lower)
        self._kt = 0               # per-function compiler-temp counter

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def compile(self, ir: dict, out_dir: str) -> None:
        # First pass: collect every function so we can register objectives.
        fns = [n for n in ir["body"] if n["op"] == "fn"]
        # `_init` creates every scoreboard objective exactly once.
        init_lines = ["scoreboard objectives add _var dummy"]
        for fn in fns:
            init_lines.append(f"scoreboard objectives add {_obj(fn['name'].lower())} dummy")
        init_lines.append("function main")

        # Second pass: compile each function body.
        for fn in fns:
            self._func = fn["name"].lower()
            self._kt = 0
            body_cmds: list[str] = []
            # Parameters arrive in _arg<i> on _var; copy into the function scope.
            for i, p in enumerate(fn["params"]):
                body_cmds.append(
                    f"scoreboard players operation {p} {_obj(self._func)} = "
                    f"{_ARG(i)} _var"
                )
            self._emit_insts(fn["body"], body_cmds)
            self.files[fn_id(fn["name"])] = body_cmds

        self.files["_init"] = init_lines
        self._write(out_dir)

    # ------------------------------------------------------------------ #
    # Objective / naming helpers
    # ------------------------------------------------------------------ #
    @property
    def _obj(self) -> str:
        return _obj(self._func)

    def _obj_for(self, varname: str) -> str:
        if varname == _RET or varname.startswith(_ARG_PREFIX):
            return "_var"
        return _obj(self._func)

    def _temp(self) -> str:
        name = f"_k{self._kt}"
        self._kt += 1
        return name

    def _new_block(self, kind: str, owner: str) -> tuple[str, list[str]]:
        self._counter += 1
        path = f"{owner}/{kind}/{self._counter}"
        cmds: list[str] = []
        self.files[path] = cmds
        return path, cmds

    # ------------------------------------------------------------------ #
    # Instruction dispatch
    # ------------------------------------------------------------------ #
    def _emit_insts(self, instrs: list[dict], out: list[str]) -> None:
        for inst in instrs:
            self._emit_one(inst, out)

    def _emit_one(self, inst: dict, out: list[str]) -> None:
        op = inst["op"]
        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            raise NotImplementedError(f"No mcfunction handler for IR op '{op}'")
        handler(inst, out)

    # -- values --------------------------------------------------------- #
    def _fmt_lit(self, v: dict) -> str:
        kind = v.get("kind")
        val = v["value"]
        if kind == "boolean":
            return "1" if val else "0"
        if kind == "int":
            return str(val)
        if kind in ("float", "string"):
            raise NotImplementedError(
                f"literal {val!r} cannot be stored in a scoreboard"
            )
        if isinstance(val, bool):
            return "1" if val else "0"
        if isinstance(val, int):
            return str(val)
        raise NotImplementedError(f"cannot use literal {val!r} as a scoreboard value")

    def _num(self, v):
        if isinstance(v, bool):
            return 1 if v else 0
        return v

    def _load(self, value: dict, dest: str, obj: str, out: list[str]) -> None:
        """Emit commands so that ``dest`` (in ``obj``) takes on ``value``."""
        if value["type"] == "literal":
            out.append(f"scoreboard players set {dest} {obj} {self._fmt_lit(value)}")
        else:
            src = value["name"]
            sobj = self._obj_for(src)
            out.append(
                f"scoreboard players operation {dest} {obj} = {src} {sobj}"
            )

    # -- set ------------------------------------------------------------ #
    def _op_set(self, inst: dict, out: list[str]) -> None:
        self._load(inst["value"], inst["target"], self._obj, out)

    # -- arithmetic ----------------------------------------------------- #
    def _op_add(self, inst, out): self._arith("add", inst, out)
    def _op_sub(self, inst, out): self._arith("sub", inst, out)
    def _op_mul(self, inst, out): self._arith("mul", inst, out)
    def _op_div(self, inst, out): self._arith("div", inst, out)
    def _op_mod(self, inst, out): self._arith("mod", inst, out)

    def _arith(self, name: str, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        obj = self._obj
        left = inst["left"]
        right = inst["right"]
        # Avoid a redundant self-copy when the target equals the left operand.
        if not (left["type"] == "var" and left["name"] == target):
            self._load(left, target, obj, out)
        if right["type"] == "literal":
            if name in _ARITH_LITERAL_CMD:
                sub = _ARITH_LITERAL_CMD[name]
                out.append(
                    f"scoreboard players {sub} {target} {obj} {self._fmt_lit(right)}"
                )
            else:
                tmp = self._temp()
                out.append(f"scoreboard players set {tmp} {obj} {self._fmt_lit(right)}")
                out.append(
                    f"scoreboard players operation {target} {obj} "
                    f"{_ARITH_SYMBOL[name]}= {tmp} {obj}"
                )
        else:
            src = right["name"]
            sobj = self._obj_for(src)
            out.append(
                f"scoreboard players operation {target} {obj} "
                f"{_ARITH_SYMBOL[name]}= {src} {sobj}"
            )

    # -- unary ---------------------------------------------------------- #
    def _op_neg(self, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        obj = self._obj
        opnd = inst["operand"]
        out.append(f"scoreboard players set {target} {obj} 0")
        if opnd["type"] == "var":
            sobj = self._obj_for(opnd["name"])
            out.append(
                f"scoreboard players operation {target} {obj} -= "
                f"{opnd['name']} {sobj}"
            )
        else:
            out.append(
                f"scoreboard players remove {target} {obj} {self._fmt_lit(opnd)}"
            )

    def _op_not(self, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        obj = self._obj
        opnd = inst["operand"]
        if opnd["type"] == "var":
            sobj = self._obj_for(opnd["name"])
            out.append(f"scoreboard players set {target} {obj} 0")
            out.append(
                f"execute unless score {opnd['name']} {sobj} matches 1.. "
                f"run scoreboard players set {target} {obj} 1"
            )
        else:
            truth = _truthy(opnd["value"], opnd.get("kind"))
            out.append(
                f"scoreboard players set {target} {obj} {0 if truth else 1}"
            )

    # -- comparisons ---------------------------------------------------- #
    def _op_eq(self, i, o): self._cmp("eq", i, o)
    def _op_ne(self, i, o): self._cmp("ne", i, o)
    def _op_lt(self, i, o): self._cmp("lt", i, o)
    def _op_gt(self, i, o): self._cmp("gt", i, o)
    def _op_le(self, i, o): self._cmp("le", i, o)
    def _op_ge(self, i, o): self._cmp("ge", i, o)

    def _cmp(self, op: str, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        obj = self._obj
        left, right = inst["left"], inst["right"]
        if left["type"] == "literal" and right["type"] == "literal":
            a = self._num(left["value"])
            b = self._num(right["value"])
            out.append(
                f"scoreboard players set {target} {obj} "
                f"{1 if _eval_const(op, a, b) else 0}"
            )
            return
        mode, cond = self._condition(op, left, right)
        out.append(f"scoreboard players set {target} {obj} 0")
        out.append(
            f"execute {mode} {cond} run scoreboard players set {target} {obj} 1"
        )

    def _condition(self, op: str, left: dict, right: dict) -> tuple[str, str]:
        """Return (execute-mode, score-clause) for ``left op right`` being true."""
        if left["type"] == "literal" and right["type"] == "literal":
            a = self._num(left["value"])
            b = self._num(right["value"])
            return (
                "if" if _eval_const(op, a, b) else "unless",
                "score _ret _var matches 1",
            )
        if left["type"] == "var" and right["type"] == "var":
            mode, sym = _CMP_TWO[op]
            lo = self._obj_for(left["name"])
            ro = self._obj_for(right["name"])
            return (mode, f"score {left['name']} {lo} {sym} {right['name']} {ro}")
        # Exactly one side literal.
        if left["type"] == "var" and right["type"] == "literal":
            var, litv = left["name"], right["value"]
        elif left["type"] == "literal" and right["type"] == "var":
            var, litv = right["name"], left["value"]
            op = _CMP_REVERSE[op]
        else:
            raise NotImplementedError("comparison with non-scalar operand")
        vobj = self._obj_for(var)
        mode, rng = _CMP_LIT[op]
        return (mode, f"score {var} {vobj} matches {rng(self._num(litv))}")

    # -- logical -------------------------------------------------------- #
    def _op_and(self, inst, out): self._logic("and", inst, out)
    def _op_or(self, inst, out):  self._logic("or", inst, out)

    def _logic(self, name: str, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        obj = self._obj

        def mat(v):
            if v["type"] == "var":
                return v["name"]
            t = self._temp()
            out.append(f"scoreboard players set {t} {obj} {self._fmt_lit(v)}")
            return t

        l = mat(inst["left"])
        r = mat(inst["right"])
        lo = self._obj_for(l)
        ro = self._obj_for(r)
        out.append(f"scoreboard players set {target} {obj} 0")
        if name == "and":
            out.append(
                f"execute if score {l} {lo} matches 1.. if score {r} {ro} matches 1.. "
                f"run scoreboard players set {target} {obj} 1"
            )
        else:
            out.append(
                f"execute if score {l} {lo} matches 1.. "
                f"run scoreboard players set {target} {obj} 1"
            )
            out.append(
                f"execute if score {r} {ro} matches 1.. "
                f"run scoreboard players set {target} {obj} 1"
            )

    # -- member / index (future work) ----------------------------------- #
    def _op_get_member(self, inst, out):
        raise NotImplementedError(member_index_error(inst))
    def _op_set_member(self, inst, out):
        raise NotImplementedError(member_index_error(inst))
    def _op_get_index(self, inst, out):
        raise NotImplementedError(member_index_error(inst))
    def _op_set_index(self, inst, out):
        raise NotImplementedError(member_index_error(inst))

    # -- block (bare, inlined) ----------------------------------------- #
    def _op_block(self, inst: dict, out: list[str]) -> None:
        self._emit_insts(inst["body"], out)

    # -- calls / print / return ---------------------------------------- #
    def _op_call(self, inst: dict, out: list[str]) -> None:
        callee = inst["callee"]
        if callee in _BUILTINS:
            self._builtin(callee, inst, out)
            return
        fid = fn_id(callee)
        # Place args into global _arg0.. slots on _var, then run the function.
        for i, arg in enumerate(inst["args"]):
            self._load(arg, _ARG(i), "_var", out)
        target = inst["target"]
        if target == "_":
            # Discarded result: just call.
            out.append(f"function {fid}")
        else:
            # Callee copies its _ret into _var; read it back here.
            tobj = self._obj_for(target)
            out.append(f"function {fid}")
            out.append(
                f"scoreboard players operation {target} {tobj} = {_RET} _var"
            )

    def _op_print(self, inst: dict, out: list[str]) -> None:
        out.append(f"tellraw @a [{self._tellraw_json(inst['value'])}]")

    def _builtin(self, name: str, inst: dict, out: list[str]) -> None:
        if name == "print":
            if len(inst["args"]) != 1:
                raise SyntaxError("print expects exactly one argument")
            out.append(f"tellraw @a [{self._tellraw_json(inst['args'][0])}]")
        else:
            raise NotImplementedError(f"unknown builtin '{name}'")

    def _tellraw_json(self, v: dict) -> str:
        if v["type"] == "literal":
            kind = v.get("kind")
            val = v["value"]
            if kind == "string":
                return json.dumps(val)
            if kind == "boolean":
                return json.dumps("true" if val else "false")
            if kind in ("int", "float"):
                return json.dumps(str(val))
            return json.dumps(str(val))
        if v["type"] == "var":
            return json.dumps(
                {"score": {"name": v["name"], "objective": self._obj_for(v["name"])}}
            )
        raise NotImplementedError("tellraw argument")

    def _op_return(self, inst: dict, out: list[str]) -> None:
        val = inst.get("value")
        if val is None:
            out.append("return 0")
            return
        if val["type"] == "var":
            sobj = self._obj_for(val["name"])
            out.append(
                f"scoreboard players operation {_RET} _var = {val['name']} {sobj}"
            )
            out.append("return 0")
        elif val["type"] == "literal":
            kind = val.get("kind")
            v = val["value"]
            if kind == "int":
                out.append(f"scoreboard players set {_RET} _var {int(v)}")
            elif kind == "boolean":
                out.append(f"scoreboard players set {_RET} _var {1 if v else 0}")
            else:
                raise NotImplementedError("cannot return a float/string value")
            out.append("return 0")
        else:
            raise NotImplementedError("cannot return this value")

    def _op_break(self, inst, out):
        raise NotImplementedError("break requires loop unrolling; not implemented")

    def _op_continue(self, inst, out):
        raise NotImplementedError("continue requires loop unrolling; not implemented")

    # -- structured control flow (no gotos) ----------------------------- #
    def _op_if(self, inst: dict, out: list[str]) -> None:
        self._emit_insts(inst["cond_ops"], out)
        cond = inst["cond"]
        then_path, then_body = self._new_block("if_then", self._func)
        self._emit_insts(inst["body"], then_body)
        self._emit_if(cond, then_path, out)
        if inst.get("else"):
            else_path, else_body = self._new_block("if_else", self._func)
            self._emit_insts(inst["else"], else_body)
            self._emit_unless(cond, else_path, out)

    def _op_while(self, inst: dict, out: list[str]) -> None:
        """Desugar ``while`` into a single self-recursive body function.

        The initial condition check is inlined in the caller; the body file
        re-checks its own condition at the end and recurses if still true.
        """
        body_path, body_body = self._new_block("while_body", self._func)
        self._emit_insts(inst["body"], body_body)
        self._emit_insts(inst["cond_ops"], body_body)
        self._emit_if(inst["cond"], body_path, body_body)
        # Initial condition check and entry into the loop body.
        self._emit_insts(inst["cond_ops"], out)
        self._emit_if(inst["cond"], body_path, out)

    def _op_for(self, inst: dict, out: list[str]) -> None:
        if inst.get("single"):
            self._emit_counted_for(inst, out)
            return
        """Desugar a 3-statement ``for`` into a single self-recursive body file.

        The init runs inline in the caller; the body file contains the loop
        body, the update, the condition re-check, and a self-recursion guarded
        by the condition.
        """
        self._emit_insts(inst["init"], out)
        body_path, body_body = self._new_block("for_body", self._func)
        self._emit_insts(inst["body"], body_body)
        self._emit_insts(inst["update"], body_body)
        self._emit_insts(inst["cond_ops"], body_body)
        self._emit_if(inst["cond"], body_path, body_body)
        # Initial condition check (after init) and entry into the loop body.
        self._emit_insts(inst["cond_ops"], out)
        self._emit_if(inst["cond"], body_path, out)

    def _emit_counted_for(self, inst: dict, out: list[str]) -> None:
        """Desugar ``for (expr)`` -- a *counted* loop that runs ``expr`` times.

        Merges the loop controller and body into a single self-recursive
        function: the body runs, decrements the private counter, and re-enters
        itself while the counter is still > 0.  This avoids a separate
        ``for_ctrl`` file.
        """
        obj = self._obj
        count_val = inst["cond"]
        ctr = self._temp()
        # Initialize the private counter to the count expression value.
        self._load(count_val, ctr, obj, out)
        body_path, body_body = self._new_block("for_body", self._func)
        self._emit_insts(inst["body"], body_body)
        # Decrement the counter at the end of each iteration.
        body_body.append(f"scoreboard players remove {ctr} {obj} 1")
        # Self-recurse while the counter is still > 0.
        ctr_val = {"type": "var", "name": ctr}
        self._emit_if(ctr_val, body_path, body_body)
        # Enter the loop.
        out.append(f"function {body_path}")

    def _emit_if(self, cond, target: str, out: list[str]) -> None:
        """Append a command that runs ``target`` when ``cond`` is truthy.

        ``cond`` is an IR Value: either ``{"type":"literal","value":...}`` or
        ``{"type":"var","name":"x"}``.
        """
        if cond is None:
            out.append(f"function {target}")
            return
        if cond["type"] == "literal":
            if _truthy(cond["value"], cond.get("kind")):
                out.append(f"function {target}")
            return
        # cond["type"] == "var"
        obj = self._obj_for(cond["name"])
        out.append(
            f"execute if score {cond['name']} {obj} matches 1.. run function {target}"
        )

    def _emit_unless(self, cond, target: str, out: list[str]) -> None:
        if cond is None:
            return
        if cond["type"] == "literal":
            if not _truthy(cond["value"], cond.get("kind")):
                out.append(f"function {target}")
            return
        obj = self._obj_for(cond["name"])
        out.append(
            f"execute unless score {cond['name']} {obj} matches 1.. run function {target}"
        )

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    def _write(self, out_dir: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        for name, lines in self.files.items():
            path = os.path.join(out_dir, f"{name}.mcfunction")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #
def _obj(func: str) -> str:
    return f"_var_{func}"


def _san(name: str) -> str:
    """Sanitise a name into a valid Minecraft objective identifier."""
    keep = name.lower()
    out = "".join(c if (c.isalnum() or c in "_-") else "_" for c in keep)
    return out or "_main"


def fn_id(name: str) -> str:
    return _san(name)


def _num(v):
    if isinstance(v, bool):
        return 1 if v else 0
    return v


def _truthy(v, kind=None) -> bool:
    if kind == "boolean":
        return bool(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return bool(v)


def _eval_const(op: str, a, b) -> bool:
    return {
        "eq": a == b, "ne": a != b, "lt": a < b,
        "gt": a > b, "le": a <= b, "ge": a >= b,
    }[op]


def member_index_error(inst: dict) -> str:
    return f"member/index access ('{inst['op']}') has no mcfunction target yet"


def compile_source(source: str, out_dir: str) -> None:
    """Parse, lower to IR and compile .mcl source into ``out_dir``."""
    ast = Parser(source).parse()
    ir = lower(ast)
    MCCompiler().compile(ir, out_dir)


def compile_ast(ir: dict, out_dir: str) -> None:
    """Compile an already-lowered IR program into ``out_dir``."""
    MCCompiler().compile(ir, out_dir)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "example.mcl"
    out = sys.argv[2] if len(sys.argv) > 2 else "out"
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        compile_source(text, out)
    except NotImplementedError as exc:
        print(f"CompileError: {exc}", file=sys.stderr)
        sys.exit(1)
    except SyntaxError as exc:
        print(f"CompileError: {exc}", file=sys.stderr)
        sys.exit(1)

