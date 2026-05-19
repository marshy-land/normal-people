"""Simple math-based CAPTCHA. Graphical version can be added later via Pillow."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Challenge:
    question: str
    answer: str


def generate_math_challenge() -> Challenge:
    a, b = random.randint(2, 9), random.randint(2, 9)
    op = random.choice(["+", "-", "*"])
    if op == "+":
        ans = a + b
    elif op == "-":
        # ensure positive
        if b > a:
            a, b = b, a
        ans = a - b
    else:
        ans = a * b
    return Challenge(question=f"{a} {op} {b} = ?", answer=str(ans))
