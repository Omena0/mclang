from __future__ import annotations

"""Compile a lowered .mcl IR program into a directory of ``.mcfunction`` files.

Target: Minecraft Java 1.21.4.

Storage-based variable model
----------------------------
Variables are stored in command storage ``mypack:mem``, with each function
getting its own namespace: ``mypack:mem.<func>.<varname>``.  This allows
any NBT type (int, float, bool, string, list, dict) to be stored directly,
without scoreboard limitations.

Scoreboard objectives are used only for temporary values during arithmetic
and comparisons: the global ``_var`` objective holds temps like ``_t0``,
``_t1``, etc.

Calling convention
------------------
Arguments are passed through global storage slots ``mypack:mem._arg0`` ...
``mypack:mem._argN``.  The callee copies them into its own namespace on
entry.  Return values are written to ``mypack:mem._ret`` by the callee.

Functions are invoked as::

    function <name> with storage mypack:mem.<name>

Macro substitution
------------------
The compiler emits ``$(varname)`` macros in commands that need direct path
insertion.  The caller's ``.mcfunction`` file must define these macros, or
the generated commands embed the full storage paths directly.
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

# IR comparison opcode -> (execute-mode, score-operator) for temp-vs-temp.
_CMP_TWO = {
    "eq": ("if", "="),
    "ne": ("unless", "="),
    "lt": ("if", "<"),
    "gt": ("if", ">"),
    "le": ("unless", ">"),
    "ge": ("unless", "<"),
}

# Comparison opcode -> (execute-mode, range-builder) for temp op literal.
_CMP_LIT = {
    "eq": ("if", lambda v: f"{v}"),
    "ne": ("unless", lambda v: f"{v}"),
    "lt": ("if", lambda v: f"..{v - 1}"),
    "gt": ("if", lambda v: f"{v + 1}.."),
    "le": ("if", lambda v: f"..{v}"),
    "ge": ("if", lambda v: f"{v}.."),
}

# Literal-on-the-left comparison opcodes flip to the equivalent `temp op lit`.
_CMP_REVERSE = {
    "eq": "eq", "ne": "ne", "lt": "gt", "gt": "lt", "le": "ge", "ge": "le",
}

_BUILTINS = {"print"}

# Reserved global variable names in storage.
_ARG_PREFIX = "_arg"
_RET = "_ret"

# Storage root for all variables.
_STORAGE_ROOT = "mypack:mem"


def _ARG(i: int) -> str:
    """Return the name of the i-th call-argument slot in storage."""
    return f"{_ARG_PREFIX}{i}"


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #
class MCCompiler:
    def __init__(self) -> None:
        self.files: dict[str, list[str]] = {}
        self._counter = 0
        self._func = ""
        self._kt = 0
        self._types: dict[str, str] = {}
        self._str_var_map: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def compile(self, ir: dict, out_dir: str) -> None:
        fns = [n for n in ir["body"] if n["op"] == "fn"]

        # _init calls main; if main was fully inlined (no separate call needed),
        # its body is inlined directly into _init.
        self.files["_init"] = ["function main"]

        for fn in fns:
            self._func = fn["name"].lower()
            self._kt = 0
            self._types = {}
            self._str_var_map = {}
            self._str_kt = 0
            body_cmds: list[str] = []
            # Copy args from global storage slots into function-local storage.
            for i, p in enumerate(fn["params"]):
                body_cmds.append(
                    f"data modify storage {_STORAGE_ROOT} {self._func}.{p} set from storage {_STORAGE_ROOT} {_ARG(i)}"
                )
            self._emit_insts(fn["body"], body_cmds)
            self.files[fn_id(fn["name"])] = body_cmds

        self._write(out_dir)

    # ------------------------------------------------------------------ #
    # Storage / naming helpers
    # ------------------------------------------------------------------ #
    def _var_path(self, varname: str) -> str | None:
        """Return the full storage path for a variable, or None for scoreboard temps."""
        if varname == _RET or varname.startswith(_ARG_PREFIX):
            return f"storage {_STORAGE_ROOT} {varname}"
        if varname.startswith("_t"):
            return None
        return f"storage {_STORAGE_ROOT} {self._func}.{varname}"

    def _temp(self) -> str:
        name = f"_t{self._kt}"
        self._kt += 1
        return name

    def _str_temp(self) -> str:
        name = f"_s{self._str_kt}"
        self._str_kt += 1
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
            return str(int(val))
        if kind in ("float", "string") or isinstance(val, (float, str)):
            return json.dumps(val)
        if isinstance(val, bool):
            return "1" if val else "0"
        if isinstance(val, int):
            return str(val)
        raise NotImplementedError(f"cannot use literal {val!r}")

    def _num(self, v):
        if isinstance(v, bool):
            return 1 if v else 0
        return v

    def _load_to_temp(self, value: dict, temp: str, out: list[str]) -> None:
        """Load a value from storage or scoreboard temp into a scoreboard temp."""
        if value["type"] == "literal":
            out.append(f"scoreboard players set {temp} _var {self._fmt_lit(value)}")
            return
        path = self._var_path(value["name"])
        if path is None:
            out.append(f"scoreboard players operation {temp} _var = {value['name']} _var")
        else:
            out.append(
                f"execute store result score {temp} _var run data get {path} 1"
            )

    def _store_from_temp(self, temp: str, varname: str, out: list[str]) -> None:
        """Store a scoreboard temp value into a storage variable or scoreboard temp."""
        path = self._var_path(varname)
        if path is None:
            if temp != varname:
                out.append(f"scoreboard players operation {varname} _var = {temp} _var")
            return
        out.append(
            f"execute store result {path} run scoreboard players get {temp} _var"
        )

    def _set_storage_cmd(self, varname: str, value: dict) -> str:
        """Return a command to set a storage variable to a literal value."""
        path = self._var_path(varname)
        kind = value.get("kind")
        val = value["value"]
        if kind == "string" or isinstance(val, str):
            return f"data modify {path} set value {json.dumps(val)}"
        if kind == "boolean" or isinstance(val, bool):
            return f"data modify {path} set value {1 if val else 0}"
        if kind in ("int", "float") or isinstance(val, (int, float)):
            return f"data modify {path} set value {int(val) if isinstance(val, (int, float)) else val}"
        return f"data modify {path} set value {json.dumps(val)}"

    def _copy_var(self, target: str, src: str, out: list[str]) -> None:
        tgt_path = self._var_path(target)
        src_path = self._var_path(src)
        if src_path is None:
            if tgt_path is None:
                out.append(f"scoreboard players operation {target} _var = {src} _var")
            else:
                out.append(
                    f"execute store result {tgt_path} run scoreboard players get {src} _var"
                )
        elif tgt_path is None:
            out.append(
                f"execute store result score {target} _var run data get {src_path} 1"
            )
        else:
            out.append(f"data modify {tgt_path} set from {src_path}")

    # -- set ------------------------------------------------------------ #
    def _op_set(self, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        value = inst["value"]
        is_temp = target.startswith("_t")
        if value["type"] == "literal":
            val = value.get("value")
            if isinstance(val, str):
                self._types[target] = "string"
                if is_temp:
                    sv = self._str_temp()
                    self._str_var_map[target] = sv
                    path = self._var_path(sv)
                    out.append(f"data modify {path} set value {json.dumps(val)}")
                else:
                    out.append(self._set_storage_cmd(target, value))
            elif isinstance(val, (int, float)):
                self._types[target] = "number"
                if is_temp:
                    out.append(f"scoreboard players set {target} _var {int(val) if isinstance(val, (int, float)) else val}")
                else:
                    out.append(self._set_storage_cmd(target, value))
            else:
                self._types[target] = "unknown"
                if is_temp:
                    out.append(f"scoreboard players set {target} _var {json.dumps(val)}")
                else:
                    out.append(self._set_storage_cmd(target, value))
        elif value["type"] == "fstring":
            self._types[target] = "string"
            parts = value["parts"]
            if all(p["type"] == "literal" for p in parts):
                literal = {"type": "literal", "kind": "string", "value": "".join(p["value"] for p in parts)}
                out.append(self._set_storage_cmd(target, literal))
            else:
                components = []
                for part in parts:
                    components.extend(self._value_to_components(part))
                if target.startswith("_t"):
                    sv = self._str_temp()
                    self._str_var_map[target] = sv
                    path = self._var_path(sv)
                else:
                    path = self._var_path(target)
                out.append(f"data modify {path} set value {json.dumps(components, separators=(',', ':'))}")
        elif value["type"] == "var":
            src = value["name"]
            if src in self._types:
                self._types[target] = self._types[src]
            if is_temp:
                src_path = self._var_path(src)
                if src_path is None:
                    out.append(f"scoreboard players operation {target} _var = {src} _var")
                else:
                    out.append(
                        f"execute store result score {target} _var run data get {src_path} 1"
                    )
            else:
                self._copy_var(target, src, out)
        else:
            self._types[target] = "unknown"
            src = value["name"]
            src_path = self._var_path(src)
            tgt_path = self._var_path(target)
            temp = self._temp()
            out.append(
                f"execute store result score {temp} _var run data get {src_path} 1"
            )
            out.append(
                f"execute store result {tgt_path} run scoreboard players get {temp} _var"
            )

    # -- arithmetic ----------------------------------------------------- #
    def _op_add(self, inst, out):
        if self._is_string_add(inst):
            self._str_concat(inst, out)
        else:
            self._arith("add", inst, out)

    def _is_string_add(self, inst: dict) -> bool:
        left = inst.get("left", {})
        right = inst.get("right", {})

        return self._is_str_value(left) or self._is_str_value(right)

    def _is_str_value(self, v: dict) -> bool:
        if v.get("type") == "literal" and isinstance(v.get("value"), str):
            return True
        if v.get("type") == "var" and self._types.get(v["name"]) == "string":
            return True
        if v.get("type") == "fstring":
            return True
        return False

    def _is_num_value(self, v: dict) -> bool:
        if v.get("type") == "literal" and isinstance(v.get("value"), (int, float)):
            return True
        if v.get("type") == "var" and self._types.get(v["name"]) == "number":
            return True
        return False

    def _str_concat(self, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        left = inst["left"]
        right = inst["right"]
        self._types[target] = "string"

        components = []
        for part in (left, right):
            components.extend(self._value_to_components(part))

        if target.startswith("_t"):
            if target in self._str_var_map:
                path = self._var_path(self._str_var_map[target])
            else:
                sv = self._str_temp()
                self._str_var_map[target] = sv
                path = self._var_path(sv)
        else:
            path = self._var_path(target)
        out.append(f"data modify {path} set value {json.dumps(components, separators=(',', ':'))}")

    def _op_sub(self, inst, out): self._arith("sub", inst, out)
    def _op_mul(self, inst, out):
        if self._is_str_mul(inst):
            self._emit_str_mul(inst, out)
        else:
            self._arith("mul", inst, out)

    def _is_str_mul(self, inst: dict) -> bool:
        left = inst.get("left", {})
        right = inst.get("right", {})
        return (self._is_str_value(left) and self._is_num_value(right)) or \
               (self._is_num_value(left) and self._is_str_value(right))

    def _emit_str_mul(self, inst: dict, out: list[str]) -> None:
        """Multiply a string by an integer using binary exponentiation.

        Algorithm: result = "", power = original
        while n > 0:
            if n & 1: result = result + power
            n >>= 1
            if n > 0: power = power + power
        """
        target = inst["target"]
        left = inst["left"]
        right = inst["right"]

        if self._is_str_value(left):
            str_val, num_val = left, right
        else:
            str_val, num_val = right, left

        self._types[target] = "string"

        # Base case: for small n, just emit n copies inline
        if num_val["type"] == "literal":
            n = int(num_val["value"])
            if n <= 0:
                path = self._var_path(target)
                out.append(f"data modify {path} set value []")
                return
            if n <= 4:
                components = self._value_to_components(str_val)
                all_comps = components * n
                if target.startswith("_t"):
                    sv = self._str_temp()
                    self._str_var_map[target] = sv
                    path = self._var_path(sv)
                else:
                    path = self._var_path(target)
                out.append(
                    f"data modify {path} set value "
                    f"{json.dumps(all_comps, separators=(',', ':'))}"
                )
                return

        result_var = self._str_temp()
        power_var = self._str_temp()
        count_temp = self._temp()

        result_path = self._var_path(result_var)
        power_path = self._var_path(power_var)

        # Initialize result to empty list
        out.append(f"data modify {result_path} set value []")

        # Initialize power as a component array
        power_components = self._value_to_components(str_val)
        out.append(
            f"data modify {power_path} set value "
            f"{json.dumps(power_components, separators=(',', ':'))}"
        )

        # Load count into scoreboard temp
        if num_val["type"] == "literal":
            out.append(f"scoreboard players set {count_temp} _var {int(num_val['value'])}")
        else:
            self._load_to_temp(num_val, count_temp, out)

        # --- loop body: while count > 0 ---
        body_path, body_body = self._new_block("str_mul_body", self._func)

        # Exit if count <= 0
        body_body.append(
            f"execute unless score {count_temp} _var matches 1.. run return 0"
        )

        # if count & 1: result = result + power
        bit_temp = self._temp()
        body_body.append(
            f"scoreboard players operation {bit_temp} _var = {count_temp} _var"
        )
        body_body.append(
            f"scoreboard players operation {bit_temp} _var %= 2"
        )

        append_path, append_body = self._new_block("str_mul_append", self._func)
        append_body.append(f"data modify {result_path} append from {power_path}")
        append_body.append("return 0")
        body_body.append(
            f"execute if score {bit_temp} _var matches 1.. "
            f"run function {append_path}"
        )

        # n >>= 1
        body_body.append(
            f"scoreboard players operation {count_temp} _var /= 2"
        )

        # if n > 0: power = power + power
        dbl_path, dbl_body = self._new_block("str_mul_double", self._func)
        dbl_path2, dbl_body2 = self._new_block("str_mul_double2", self._func)
        dbl_body.append(
            f"execute if score {count_temp} _var matches 1.. "
            f"run function {dbl_path2}"
        )
        dbl_body.append("return 0")
        dbl_body2.append(f"data modify {power_path} append from {power_path}")
        dbl_body2.append("return 0")
        body_body.append(f"function {dbl_path}")

        # Recursive call
        body_body.append(f"function {body_path}")
        body_body.append("return 0")

        # Kick off the loop
        out.append(f"function {body_path}")

        # Copy result to target
        self._copy_str_to_target(target, result_path, out)

    def _copy_str_to_target(self, target: str, src_path: str, out: list[str]) -> None:
        """Copy a string component-array from src_path into target."""
        if target.startswith("_t"):
            sv = self._str_temp()
            self._str_var_map[target] = sv
            target_path = self._var_path(sv)
        else:
            target_path = self._var_path(target)
        out.append(f"data modify {target_path} set from {src_path}")
    def _op_div(self, inst, out): self._arith("div", inst, out)
    def _op_mod(self, inst, out): self._arith("mod", inst, out)

    def _arith(self, name: str, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        left = inst["left"]
        right = inst["right"]

        # Load left operand into a temp scoreboard.
        if left["type"] == "var" and left["name"] == target:
            ltemp = self._temp()
            self._load_to_temp(left, ltemp, out)
        elif left["type"] == "literal":
            ltemp = self._temp()
            out.append(f"scoreboard players set {ltemp} _var {self._fmt_lit(left)}")
        else:
            ltemp = self._temp()
            self._load_to_temp(left, ltemp, out)

        # Load right operand into a temp scoreboard.
        if right["type"] == "literal":
            rtemp = self._temp()
            out.append(f"scoreboard players set {rtemp} _var {self._fmt_lit(right)}")
        else:
            rtemp = self._temp()
            self._load_to_temp(right, rtemp, out)

        # Perform arithmetic on the temps.
        if name in _ARITH_LITERAL_CMD and right["type"] == "literal":
            out.append(
                f"scoreboard players {_ARITH_LITERAL_CMD[name]} {ltemp} _var {self._fmt_lit(right)}"
            )
        else:
            out.append(
                f"scoreboard players operation {ltemp} _var {_ARITH_SYMBOL[name]}= {rtemp} _var"
            )

        # Store result back to the target storage variable.
        self._store_from_temp(ltemp, target, out)

    # -- unary ---------------------------------------------------------- #
    def _op_neg(self, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        opnd = inst["operand"]
        temp = self._temp()
        out.append(f"scoreboard players set {temp} _var 0")
        if opnd["type"] == "var":
            self._load_to_temp(opnd, temp, out)
        else:
            out.append(
                f"scoreboard players remove {temp} _var {self._fmt_lit(opnd)}"
            )
        out.append(f"scoreboard players operation {temp} _var -= {temp} _var")
        self._store_from_temp(temp, target, out)

    def _op_not(self, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        opnd = inst["operand"]
        temp = self._temp()
        if opnd["type"] == "var":
            self._load_to_temp(opnd, temp, out)
            out.append(f"scoreboard players set {temp} _var 0")
            out.append(
                f"execute unless score {temp} _var matches 1.. "
                f"run scoreboard players set {temp} _var 1"
            )
        else:
            truth = _truthy(opnd["value"], opnd.get("kind"))
            out.append(f"scoreboard players set {temp} _var {0 if truth else 1}")
        self._store_from_temp(temp, target, out)

    # -- comparisons ---------------------------------------------------- #
    def _op_eq(self, i, o): self._cmp("eq", i, o)
    def _op_ne(self, i, o): self._cmp("ne", i, o)
    def _op_lt(self, i, o): self._cmp("lt", i, o)
    def _op_gt(self, i, o): self._cmp("gt", i, o)
    def _op_le(self, i, o): self._cmp("le", i, o)
    def _op_ge(self, i, o): self._cmp("ge", i, o)

    def _cmp(self, op: str, inst: dict, out: list[str]) -> None:
        target = inst["target"]
        left, right = inst["left"], inst["right"]
        if left["type"] == "literal" and right["type"] == "literal":
            a = self._num(left["value"])
            b = self._num(right["value"])
            out.append(
                f"scoreboard players set {target} _var "
                f"{1 if _eval_const(op, a, b) else 0}"
            )
            return

        # Load both operands into temps.
        ltemp = self._temp()
        rtemp = self._temp()
        self._load_to_temp(left, ltemp, out)
        self._load_to_temp(right, rtemp, out)

        mode, sym = _CMP_TWO[op]
        out.append(f"scoreboard players set {target} _var 0")
        out.append(
            f"execute {mode} score {ltemp} _var {sym} {rtemp} _var "
            f"run scoreboard players set {target} _var 1"
        )

    # -- logical -------------------------------------------------------- #
    def _op_and(self, inst, out): self._logic("and", inst, out)
    def _op_or(self, inst, out):  self._logic("or", inst, out)

    def _logic(self, name: str, inst: dict, out: list[str]) -> None:
        target = inst["target"]

        def mat(v):
            if v["type"] == "var":
                t = self._temp()
                self._load_to_temp(v, t, out)
                return t
            t = self._temp()
            out.append(f"scoreboard players set {t} _var {self._fmt_lit(v)}")
            return t

        l = mat(inst["left"])
        r = mat(inst["right"])
        out.append(f"scoreboard players set {target} _var 0")
        if name == "and":
            out.append(
                f"execute if score {l} _var matches 1.. if score {r} _var matches 1.. "
                f"run scoreboard players set {target} _var 1"
            )
        else:
            out.append(
                f"execute if score {l} _var matches 1.. "
                f"run scoreboard players set {target} _var 1"
            )
            out.append(
                f"execute if score {r} _var matches 1.. "
                f"run scoreboard players set {target} _var 1"
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
        self._emit_insts(inst.get("body", []), out)

    # -- calls / print / return ---------------------------------------- #
    def _op_call(self, inst: dict, out: list[str]) -> None:
        callee = inst["callee"]
        if callee in _BUILTINS:
            self._builtin(callee, inst, out)
            return
        fid = fn_id(callee)
        # Place args into global _arg<i> storage slots.
        for i, arg in enumerate(inst["args"]):
            if arg["type"] == "literal":
                out.append(self._set_storage_cmd(_ARG(i), arg))
            else:
                src_path = self._var_path(arg["name"])
                tgt_path = f"storage {_STORAGE_ROOT} {_ARG(i)}"
                temp = self._temp()
                out.append(
                    f"execute store result score {temp} _var run data get {src_path} 1"
                )
                out.append(
                    f"execute store result {tgt_path} run scoreboard players get {temp} _var"
                )
        target = inst["target"]
        if target == "_":
            out.append(f"function {fid} with storage {_STORAGE_ROOT}.{fid}")
        else:
            out.append(f"function {fid} with storage {_STORAGE_ROOT}.{fid}")
            tgt_path = self._var_path(target)
            ret_path = f"storage {_STORAGE_ROOT} {_RET}"
            temp = self._temp()
            out.append(
                f"execute store result score {temp} _var run data get {ret_path} 1"
            )
            out.append(
                f"execute store result {tgt_path} run scoreboard players get {temp} _var"
            )

    def _op_print(self, inst: dict, out: list[str]) -> None:
        out.append(f"tellraw @a {self._tellraw_json(inst['value'])}")

    def _builtin(self, name: str, inst: dict, out: list[str]) -> None:
        if name == "print":
            if len(inst["args"]) != 1:
                raise SyntaxError("print expects exactly one argument")
            out.append(f"tellraw @a {self._tellraw_json(inst['args'][0])}")
        else:
            raise NotImplementedError(f"unknown builtin '{name}'")

    def _resolve_var_to_components(self, name: str) -> list[dict]:
        """Resolve a variable name to text component(s), handling string-temp mapping."""
        if name in self._str_var_map:
            name = self._str_var_map[name]
        path = self._var_path(name)
        if path is not None and path.startswith("storage "):
            storage_path = path[len("storage "):]
            storage_name, nbt_path = storage_path.split(" ", 1)
            return [{"nbt": nbt_path, "storage": storage_name}]
        if name.startswith("_t"):
            return [{"score": {"name": name, "objective": "_var"}}]
        raise NotImplementedError(f"cannot use variable as text: {name}")

    def _value_to_components(self, v: dict) -> list[dict]:
        """Convert any IR value into a list of tellraw text components."""
        if v["type"] == "literal":
            kind = v.get("kind")
            val = v["value"]
            if kind == "string" or isinstance(val, str):
                return [{"text": val}]
            if kind == "boolean" or isinstance(val, bool):
                return [{"text": "true" if val else "false"}]
            return [{"text": str(val)}]
        if v["type"] == "var":
            return self._resolve_var_to_components(v["name"])
        if v["type"] == "fstring":
            components = []
            for part in v["parts"]:
                components.extend(self._value_to_components(part))
            return components
        raise NotImplementedError(f"cannot convert value to text: {v['type']}")

    def _tellraw_component(self, v: dict) -> dict:
        """Return the first text component for a single value."""
        comps = self._value_to_components(v)
        return comps[0] if comps else {"text": ""}

    def _tellraw_json(self, v: dict) -> str:
        """Return the full JSON array string for a ``tellraw @a [...]`` argument."""
        return json.dumps(self._value_to_components(v))

    def _op_return(self, inst: dict, out: list[str]) -> None:
        val = inst.get("value")
        if val is None:
            out.append("return 0")
            return
        if val["type"] == "var":
            src = val["name"]
            ret_path = f"storage {_STORAGE_ROOT} {_RET}"
            if src in self._str_var_map:
                src = self._str_var_map[src]
            src_path = self._var_path(src)
            if src_path is None:
                out.append(
                    f"execute store result {ret_path} run scoreboard players get {src} _var"
                )
            else:
                temp = self._temp()
                out.append(
                    f"execute store result score {temp} _var run data get {src_path} 1"
                )
                out.append(
                    f"execute store result {ret_path} run scoreboard players get {temp} _var"
                )
            out.append("return 0")
        elif val["type"] == "literal":
            out.append(
                f"data modify storage {_STORAGE_ROOT} {_RET} set value {self._fmt_lit(val)}"
            )
            out.append("return 0")
        else:
            raise NotImplementedError("cannot return this value")

    def _op_break(self, inst, out):
        out.append(f"data modify storage {_STORAGE_ROOT} {self._func}._break set value 1")
        out.append("return 0")

    def _op_continue(self, inst, out):
        out.append("return 0")

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

        ``break`` sets a ``_break`` flag in storage and returns; the body
        checks that flag on entry and exits early if it is set.
        """
        body_path, body_body = self._new_block("while_body", self._func)

        break_temp = self._temp()
        self._load_to_temp({"type": "var", "name": "_break"}, break_temp, body_body)
        body_body.append(
            f"execute if score {break_temp} _var matches 1.. run return 0"
        )
        self._emit_insts(inst["body"], body_body)
        self._emit_insts(inst["cond_ops"], body_body)
        self._emit_if(inst["cond"], body_path, body_body)
        body_body.append("return 0")

        self._emit_insts(inst["cond_ops"], out)
        self._emit_if(inst["cond"], body_path, out)

    def _op_for(self, inst: dict, out: list[str]) -> None:
        if inst.get("single"):
            self._emit_counted_for(inst, out)
            return

        body_path, body_body = self._new_block("for_body", self._func)

        break_temp = self._temp()
        self._load_to_temp({"type": "var", "name": "_break"}, break_temp, body_body)
        body_body.append(
            f"execute if score {break_temp} _var matches 1.. run return 0"
        )
        self._emit_insts(inst["body"], body_body)
        self._emit_insts(inst["update"], body_body)
        self._emit_insts(inst["cond_ops"], body_body)
        self._emit_if(inst["cond"], body_path, body_body)
        body_body.append("return 0")

        self._emit_insts(inst["init"], out)
        self._emit_insts(inst["cond_ops"], out)
        self._emit_if(inst["cond"], body_path, out)

    def _emit_counted_for(self, inst: dict, out: list[str]) -> None:
        """Desugar ``for (expr)`` -- a *counted* loop."""
        count_val = inst["cond"]
        ctr = self._temp()
        self._load_to_temp(count_val, ctr, out)

        body_path, body_body = self._new_block("for_body", self._func)
        break_temp = self._temp()
        self._load_to_temp({"type": "var", "name": "_break"}, break_temp, body_body)
        body_body.append(
            f"execute if score {break_temp} _var matches 1.. run return 0"
        )
        self._emit_insts(inst["body"], body_body)
        body_body.append(f"scoreboard players remove {ctr} _var 1")
        ctr_val = {"type": "var", "name": ctr}
        self._emit_if(ctr_val, body_path, body_body)
        body_body.append("return 0")

        out.append(f"function {body_path}")

    def _emit_if(self, cond, target: str, out: list[str]) -> None:
        """Append a command that runs ``target`` when ``cond`` is truthy."""
        if cond is None:
            out.append(f"function {target}")
            return
        if cond["type"] == "literal":
            if _truthy(cond["value"], cond.get("kind")):
                out.append(f"function {target}")
            return
        temp = self._temp()
        self._load_to_temp(cond, temp, out)
        out.append(
            f"execute if score {temp} _var matches 1.. run function {target}"
        )

    def _emit_unless(self, cond, target: str, out: list[str]) -> None:
        if cond is None:
            return
        if cond["type"] == "literal":
            if not _truthy(cond["value"], cond.get("kind")):
                out.append(f"function {target}")
            return
        temp = self._temp()
        self._load_to_temp(cond, temp, out)
        out.append(
            f"execute unless score {temp} _var matches 1.. run function {target}"
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
def fn_id(name: str) -> str:
    keep = name.lower()
    out = "".join(c if (c.isalnum() or c in "_-") else "_" for c in keep)
    return out or "_main"


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
