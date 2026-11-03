"""
title: Calculator
description: Calculate the result of a mathematical equation.
requirements: pydantic
version: 0.1.0
"""

from __future__ import annotations

import ast
import math
import operator

from pydantic import Field


# Reject inputs long enough to be used as a resource bomb.
_MAX_EXPRESSION_LENGTH = 1000
# Reject nesting deep enough to overflow the call stack.
_MAX_DEPTH = 100
# Reject exponents that would build enormous integers.
_MAX_ABS_EXPONENT = 1000

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": pow,
}

_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


class Tools:
    def __init__(self):
        pass

    def _eval_node(self, node: ast.AST, depth: int = 0) -> int | float:
        """
        Evaluate one node from a parsed arithmetic expression.

        :param node: An AST node produced in eval mode.
        :param depth: Current nesting depth.
        :return: The numeric value of the node.
        """
        if depth > _MAX_DEPTH:
            raise ValueError("Expression is too deeply nested")

        if isinstance(node, ast.Expression):
            return self._eval_node(node.body, depth + 1)

        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value

        if isinstance(node, ast.BinOp):
            op = _BINOPS.get(type(node.op))
            if op is None:
                raise ValueError("Unsupported operator")
            left = self._eval_node(node.left, depth + 1)
            right = self._eval_node(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                self._check_power(right)
            return op(left, right)

        if isinstance(node, ast.UnaryOp):
            op = _UNARYOPS.get(type(node.op))
            if op is None:
                raise ValueError("Unsupported operator")
            return op(self._eval_node(node.operand, depth + 1))

        if isinstance(node, ast.Name):
            if node.id not in _CONSTANTS:
                raise ValueError("Unknown name")
            return _CONSTANTS[node.id]

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            func = _FUNCTIONS.get(node.func.id)
            if func is None or node.keywords:
                raise ValueError("Unsupported function")
            args = [self._eval_node(arg, depth + 1) for arg in node.args]
            return func(*args)

        raise ValueError("Unsupported syntax")

    def _check_power(self, exponent: int | float) -> None:
        """
        Reject exponents large enough to exhaust memory.

        :param exponent: The already-evaluated exponent.
        :return: None.
        """
        if isinstance(exponent, float) and not exponent.is_integer():
            if abs(exponent) > _MAX_ABS_EXPONENT:
                raise ValueError("Exponent is too large")
            return
        if abs(int(exponent)) > _MAX_ABS_EXPONENT:
            raise ValueError("Exponent is too large")

    def calculator(
        self,
        equation: str = Field(
            ..., description="The mathematical equation to calculate."
        ),
    ) -> str:
        """
        Calculate the result of an equation.

        Arithmetic, parentheses, common math functions, and pi, e, and tau are allowed.
        Names, attribute access, and other Python syntax are rejected.

        :param equation: The mathematical equation to calculate.
        :return: The equation and its result, or ``Invalid equation``.
        """
        try:
            if not isinstance(equation, str):
                return "Invalid equation"
            if not equation.strip() or len(equation) > _MAX_EXPRESSION_LENGTH:
                return "Invalid equation"
            tree = ast.parse(equation, mode="eval")
            result = self._eval_node(tree)
            return f"{equation} = {result}"
        except Exception:
            return "Invalid equation"
