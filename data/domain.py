DIGITS= [str(i) for i in range(0, 10)]
OPERATORS = list('+-*/')
PARENTHESES = list('()')
START = '<START>'
END = '<END>'
NULL = '<NULL>'
SYMBOLS = DIGITS + OPERATORS + PARENTHESES
# SYM2ID = {v:i for i, v in enumerate(SYMBOLS)}
# ID2SYM = {i:v for i, v in enumerate(SYMBOLS)}
SYM2ID = lambda x: SYMBOLS.index(x)
ID2SYM = lambda x: SYMBOLS[x]

import math
from inspect import signature
class Program():
    def __init__(self, fn=None):
        self.fn = fn
        self.arity = len(signature(fn).parameters) if fn is not None else 0
        self.likelihood = 1.0
        self.priority = 1.0

    def __call__(self, *inputs):
        if len(inputs) != self.arity or None in inputs:
            raise TypeError
        res = self.fn(*inputs)
        return res

functions = [
    lambda: 0, lambda: 1, lambda: 2, lambda: 3, lambda: 4, lambda: 5, lambda: 6, lambda: 7, lambda: 8, lambda: 9,
    lambda x,y: x+y, lambda x,y: max(0, x-y), lambda x,y: x*y, lambda x,y: math.ceil(x/y) if y != 0 else None, 
    lambda: None, lambda: None,
]

PROGRAMS = [Program(f) for f in functions] 
SYM2PROG= {s:p for s, p in zip(SYMBOLS, PROGRAMS)}