"""
title: Calculator tests
description: Tests for arithmetic evaluation in calculator.py.
requirements: pytest
version: 0.1.0
"""

import math

from calculator import Tools


tools = Tools()
calculator = tools.calculator


def test_basic_arithmetic():
    assert calculator("2 + 2") == "2 + 2 = 4"
    assert calculator("10 - 3 * 2") == "10 - 3 * 2 = 4"
    assert calculator("(10 - 3) * 2") == "(10 - 3) * 2 = 14"
    assert calculator("-5 + 2") == "-5 + 2 = -3"
    assert calculator("7 / 2") == "7 / 2 = 3.5"
    assert calculator("7 // 2") == "7 // 2 = 3"
    assert calculator("8 % 3") == "8 % 3 = 2"
    assert calculator("2 ** 8") == "2 ** 8 = 256"
    assert calculator("1e-3") == "1e-3 = 0.001"


def test_functions_and_constants():
    assert calculator("sqrt(81)") == "sqrt(81) = 9.0"
    assert calculator("abs(-4) + round(1.6)") == "abs(-4) + round(1.6) = 6"
    assert calculator("min(3, 1, 2)") == "min(3, 1, 2) = 1"
    assert calculator("max(3, 1, 2)") == "max(3, 1, 2) = 3"
    assert calculator("floor(3.9)") == "floor(3.9) = 3"
    assert calculator("log(e)") == f"log(e) = {math.log(math.e)}"
    assert calculator("sin(pi / 2)") == f"sin(pi / 2) = {math.sin(math.pi / 2)}"


def test_invalid_equations():
    assert calculator("") == "Invalid equation"
    assert calculator("   ") == "Invalid equation"
    assert calculator("2 +") == "Invalid equation"
    assert calculator("1 / 0") == "Invalid equation"
    assert calculator("hello") == "Invalid equation"
    assert calculator("sqrt()") == "Invalid equation"
    assert calculator("2 ** 10000") == "Invalid equation"
    assert calculator("abs(" * 150 + "1" + ")" * 150) == "Invalid equation"


def test_rejects_python_execution():
    assert calculator("__import__('math').sqrt(4)") == "Invalid equation"
    assert calculator("(1).__class__") == "Invalid equation"
    assert calculator("eval('2 + 2')") == "Invalid equation"
    assert calculator("[1, 2, 3]") == "Invalid equation"
    assert calculator("open('x')") == "Invalid equation"
