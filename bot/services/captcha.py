"""Simple math-based CAPTCHA. Graphical version can be added later via Pillow."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Challenge:
    question: str
    answer: str


def generate_math_challenge() -> Challenge:
    a, b = random.randint(2, 19), random.randint(2, 19)
    op = random.choice(["+", "-"])
    if op == "+":
        ans = a + b
    else:
        # ensure positive
        if b > a:
            a, b = b, a
        ans = a - b
    return Challenge(question=f"{a} {op} {b} = ?", answer=str(ans))
