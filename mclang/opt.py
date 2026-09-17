from __future__ import annotations

"""Optional optimization passes over the lowered IR.

Planned / implemented passes
----------------------------
* ``inline_functions``: replace ``call`` sites with the callee's body when the
  callee is small and has no observable side effects beyond returning a value.
* ``constant_fold``: evaluate arithmetic / comparison instructions whose
  operands are both literals, replacing them with a single ``set`` of the
  computed literal.
* ``dead_code_elim``: remove assignments to variables that are never read
  afterward within the same function.
"""

from copy import deepcopy

# Maximum body size for a function to be eligible for inlining.
_INLINE_THRESHOLD = 4


def optimize(ir: dict, *, inline: bool = True, fold: bool = True, dce: bool = True) -> dict:
    """Run the requested optimization passes over ``ir`` and return the result."""
    ir = deepcopy(ir)
    if inline:
        ir = _inline_functions(ir)
    if fold:
        ir = _constant_fold(ir)
    if dce:
        ir = _dead_code_elim(ir)
    return ir


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _is_literal(value: dict) -> bool:
    return value.get("type") == "literal"


def _literal_value(value: dict):
    return value.get("value")


def _set_literal(target: str, value) -> dict:
    return {"op": "set", "target": target, "value": {"type": "literal", "value": value}}


def _function_bodies(ir: dict) -> dict[str, dict]:
    return {fn["name"]: fn for fn in ir.get("body", []) if fn.get("op") == "fn"}


# ------------------------------------------------------------------ #
# Constant folding
# ------------------------------------------------------------------ #
_ARITH = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mul": lambda a, b: a * b,
    "div": lambda a, b: a // b if isinstance(a, int) and isinstance(b, int) else a / b,
    "mod": lambda a, b: a % b,
}
_CMP = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b,
    "ge": lambda a, b: a >= b,
}
_LOGICAL = {
    "and": lambda a, b: bool(a) and bool(b),
    "or": lambda a, b: bool(a) or bool(b),
}


def _constant_fold(ir: dict) -> dict:
    defs: dict[str, dict] = {}

    def _value(v: dict):
        if _is_literal(v):
            return _literal_value(v)
        name = v.get("name")
        if name in defs:
            candidate = defs[name]
            if _is_literal(candidate.get("value")):
                return _literal_value(candidate["value"])
        return None

    def _fold_instrs(instrs: list[dict]) -> list[dict]:
        out: list[dict] = []
        for instr in instrs:
            op = instr.get("op")
            if op in _ARITH:
                left = _value(instr.get("left", {}))
                right = _value(instr.get("right", {}))
                if left is not None and right is not None:
                    try:
                        result = _ARITH[op](left, right)
                    except Exception:
                        result = None
                    if result is not None:
                        out.append(_set_literal(instr["target"], result))
                        defs[instr["target"]] = out[-1]
                        continue
            if op in _CMP:
                left = _value(instr.get("left", {}))
                right = _value(instr.get("right", {}))
                if left is not None and right is not None:
                    result = _CMP[op](left, right)
                    out.append(_set_literal(instr["target"], 1 if result else 0))
                    defs[instr["target"]] = out[-1]
                    continue
            if op in _LOGICAL:
                left = _value(instr.get("left", {}))
                right = _value(instr.get("right", {}))
                if left is not None and right is not None:
                    result = _LOGICAL[op](left, right)
                    out.append(_set_literal(instr["target"], 1 if result else 0))
                    defs[instr["target"]] = out[-1]
                    continue
            if op == "set" and _is_literal(instr.get("value")):
                defs[instr["target"]] = instr
            out.append(instr)
        return out

    def _walk(node):
        if node.get("op") == "fn":
            body = _fold_instrs(node.get("body", []))
            node["body"] = body
            return node
        return node

    return {
        "type": ir.get("type", "ir_program"),
        "body": [_walk(deepcopy(fn)) for fn in ir.get("body", [])],
    }


# ------------------------------------------------------------------ #
# Function inlining
# ------------------------------------------------------------------ #
def _inline_functions(ir: dict) -> dict:
    bodies = _function_bodies(ir)

    def _is_inlineable(fn: dict) -> bool:
        if fn.get("op") != "fn":
            return False
        if len(fn.get("body", [])) > _INLINE_THRESHOLD:
            return False
        return True

    def _inline_call(instr: dict) -> list[dict]:
        callee = instr.get("callee")
        if callee not in bodies or not _is_inlineable(bodies[callee]):
            return [instr]
        fn_body = deepcopy(bodies[callee]["body"])
        params = bodies[callee].get("params", [])
        args = instr.get("args", [])
        replacements: dict[str, str] = {}
        for param, expr in zip(params, args):
            if expr.get("type") == "var":
                replacements[param] = expr.get("name")
            else:
                temp = f"_t{_temp()}"
                replacements[param] = temp
                fn_body.insert(0, {"op": "set", "target": temp, "value": expr})
        out: list[dict] = []
        target = instr.get("target")
        for item in fn_body:
            if item.get("op") == "return":
                if target != "_" and item.get("value"):
                    out.append(
                        {"op": "set", "target": target, "value": item.get("value")}
                    )
                continue
            out.append(_replace_vars(item, replacements))
        return out

    def _replace_vars(instr: dict, mapping: dict[str, str]) -> dict:
        instr = deepcopy(instr)

        def _scan(value):
            if isinstance(value, dict):
                if value.get("type") == "var" and value.get("name") in mapping:
                    value["name"] = mapping[value["name"]]
                for v in value.values():
                    _scan(v)
            elif isinstance(value, list):
                for item in value:
                    _scan(item)

        _scan(instr)
        return instr

    def _walk(node):
        if node.get("op") == "fn":
            body: list[dict] = []
            for item in node.get("body", []):
                if item.get("op") == "call":
                    body.extend(_inline_call(item))
                else:
                    body.append(_walk(deepcopy(item)))
            node["body"] = body
        return node

    return {
        "type": ir.get("type", "ir_program"),
        "body": [_walk(deepcopy(fn)) for fn in ir.get("body", [])],
    }


# ------------------------------------------------------------------ #
# Dead code elimination
# ------------------------------------------------------------------ #
def _dead_code_elim(ir: dict) -> dict:
    def _used_names(instrs: list[dict]) -> set[str]:
        used: set[str] = set()

        def _scan(value):
            if isinstance(value, dict):
                if value.get("type") == "var":
                    used.add(value["name"])
                for v in value.values():
                    _scan(v)
            elif isinstance(value, list):
                for item in value:
                    _scan(item)

        for instr in instrs:
            _scan(instr)
        return used

    def _eliminate(instrs: list[dict]) -> list[dict]:
        if not instrs:
            return instrs
        used = _used_names(instrs[1:]) if len(instrs) > 1 else set()
        first = instrs[0]
        if first.get("op") == "set":
            target = first.get("target")
            if target and target not in used:
                return _eliminate(instrs[1:])
        return [first] + _eliminate(instrs[1:])

    def _walk(node):
        if node.get("op") == "fn":
            node["body"] = _eliminate(node.get("body", []))
            return node
        return node

    return {
        "type": ir.get("type", "ir_program"),
        "body": [_walk(deepcopy(fn)) for fn in ir.get("body", [])],
    }


# ------------------------------------------------------------------ #
# Unique temp counter for inlining substitutions
# ------------------------------------------------------------------ #
_temp_counter = 0


def _temp() -> int:
    global _temp_counter
    value = _temp_counter
    _temp_counter += 1
    return value
