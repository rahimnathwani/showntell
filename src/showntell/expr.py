"""Arithmetic expressions for plots, read and evaluated without `eval`: numbers, the function's
parameter, named constants, `pi`, `e`, + - * / ^ (or **, right-associative), unary minus,
parentheses, implicit multiplication (`2x`, `3(x+1)`, `(x+1)(x-1)`, `2 sin(x)`), a fixed set of
math functions, and calls to functions the plot defined earlier. Where implicit multiplication
reads two ways (`1/2x`, `e^2x`) it is refused rather than guessed, as is `log` (ln or log10?).
`parse` returns a tree that `evaluate` walks; a value that is undefined (division by zero, a log
of a negative, an overflow) is NaN, which the plot leaves as a gap.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

FUNCS: dict[str, Callable[..., float]] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "sqrt": math.sqrt,
    "cbrt": lambda v: math.copysign(abs(v) ** (1 / 3), v),
    "abs": abs,
    "exp": math.exp,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "sign": lambda v: math.copysign(1.0, v) if v else 0.0,
    "min": min,
    "max": max,
}
CONSTS = {"pi": math.pi, "π": math.pi, "e": math.e, "tau": math.tau}
MAX_DEPTH = 60
MAX_LEN = 400

_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)|([A-Za-z_π][A-Za-z_0-9]*'*)|(\*\*|[-+*/^(),·×−]))")

Node = tuple  # ("num", v) | ("var", name) | ("neg", a) | (op, a, b) | ("call", name, [args])


class ExprError(ValueError):
    pass


def tokens(src: str) -> list[tuple[str, str]]:
    out, i = [], 0
    src = src.rstrip()
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m or m.end() == i:
            bad = src[i:].strip()[:1]
            if bad == "|":
                raise ExprError("write `abs(x)` for |x|")
            raise ExprError(f"`{bad}` is not part of an expression (use + - * / ^ and parentheses)")
        num, name, op = m.groups()
        if num is not None:
            out.append(("num", num))
        elif name is not None:
            out.append(("name", name))
        else:
            out.append(("op", {"**": "^", "·": "*", "×": "*", "−": "-"}.get(op, op)))
        i = m.end()
    return out


def parse(src: str, known: set[str] | None = None, params: tuple[str, ...] = ("x",)) -> Node:
    """The expression's tree. `known`: functions defined earlier in the plot, callable by name;
    `params`: the names that stand for values (the function's parameter, the plot's constants)."""
    if len(src) > MAX_LEN:
        raise ExprError(f"the expression is over {MAX_LEN} characters")
    toks = tokens(src)
    if not toks:
        raise ExprError("the expression is empty")
    known = known or set()
    pos = 0

    def peek() -> tuple[str, str] | None:
        return toks[pos] if pos < len(toks) else None

    def take(kind: str | None = None, text: str | None = None) -> tuple[str, str]:
        nonlocal pos
        t = peek()
        if t is None or (kind and t[0] != kind) or (text and t[1] != text):
            want = f"`{text}`" if text else "a number, `x` or `(`"
            got = f"`{t[1]}`" if t else "the end"
            raise ExprError(f"expected {want} but found {got}")
        pos += 1
        return t

    def sum_(depth: int) -> Node:
        node = product(depth)
        while (t := peek()) in (("op", "+"), ("op", "-")):
            take()
            node = (t[1], node, product(depth))
        return node

    def starts_factor(t: tuple[str, str] | None) -> bool:
        return bool(t) and (t[0] in ("num", "name") or t == ("op", "("))

    def product(depth: int) -> Node:
        node = last = unary(depth)
        divided = False
        while True:
            t = peek()
            if t in (("op", "*"), ("op", "/")):
                take()
                last = unary(depth)
                node = (t[1], node, last)
                divided = t[1] == "/"
            elif starts_factor(t):  # implicit: 2x, 3(x+1), (x+1)(x-1), 2 sin(x)
                if divided:
                    raise ExprError("`1/2x` reads two ways: write `1/(2x)` or `(1/2)x`")
                if last[0] == "^" or (last[0] == "neg" and last[1][0] == "^"):
                    raise ExprError("`e^2x` reads two ways: write `e^(2x)`, or `*` after the power: `x^2 * sin(x)`")
                last = power(depth)
                node = ("*", node, last)
            else:
                return node

    def unary(depth: int) -> Node:
        if peek() == ("op", "-"):
            take()
            return ("neg", unary(depth + 1))
        if peek() == ("op", "+"):
            take()
            return unary(depth + 1)
        return power(depth)

    def power(depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise ExprError("the expression is nested too deeply")
        base = atom(depth)
        if peek() == ("op", "^"):
            take()
            return ("^", base, unary(depth + 1))  # 2^-x, and right-associative: 2^3^2 = 2^9
        return base

    def atom(depth: int) -> Node:
        t = peek()
        if t is None:
            raise ExprError("the expression ends too early")
        if t[0] == "num":
            take()
            return ("num", float(t[1]))
        if t == ("op", "("):
            take()
            node = sum_(depth + 1)
            take("op", ")")
            return node
        if t[0] == "name":
            take()
            name = t[1]
            if peek() == ("op", "(") and (name in FUNCS or name in known):
                take()
                args = [sum_(depth + 1)]
                while peek() == ("op", ","):
                    take()
                    args.append(sum_(depth + 1))
                take("op", ")")
                if name in known and len(args) != 1:
                    raise ExprError(f"`{name}` takes one value")
                if name in FUNCS and name not in ("min", "max") and len(args) != 1:
                    raise ExprError(f"`{name}` takes one value")
                return ("call", name, args)
            if name == "log":
                raise ExprError("`log` reads two ways: write `ln` (base e), `log10` or `log2`")
            if name in params:
                return ("var", name)
            if name in CONSTS:
                return ("num", CONSTS[name])
            if name in FUNCS or name in known:
                raise ExprError(f"`{name}` is a function: write `{name}(x)`")
            # `xy` or `2pix`: a run of letters is not split into products
            raise ExprError(f"unknown name `{name}`: use x, pi, e, a function such as sin(x), or one defined above")
        raise ExprError(f"`{t[1]}` cannot start a value")

    node = sum_(0)
    if pos < len(toks):
        raise ExprError(f"unexpected `{toks[pos][1]}`")
    return node


def evaluate(node: Node, env: dict[str, float], funcs: dict[str, tuple[str, Node]] | None = None, depth: int = 0) -> float:
    """The value of a tree, NaN where it is undefined. `env`: values by name; `funcs`: the plot's
    own functions as (parameter, tree)."""
    if depth > 40:
        return math.nan
    try:
        op = node[0]
        if op == "num":
            return node[1]
        if op == "var":
            return env[node[1]]
        if op == "neg":
            return -evaluate(node[1], env, funcs, depth)
        if op == "call":
            args = [evaluate(a, env, funcs, depth) for a in node[2]]
            if funcs and node[1] in funcs:
                param, body = funcs[node[1]]
                return evaluate(body, {**env, param: args[0]}, funcs, depth + 1)
            v = FUNCS[node[1]](*args)
            return float(v)
        a = evaluate(node[1], env, funcs, depth)
        b = evaluate(node[2], env, funcs, depth)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "^":
            v = a**b
            return v if isinstance(v, float) else math.nan  # a negative to a fractional power
    except (ArithmeticError, ValueError, TypeError):
        return math.nan
    return math.nan
