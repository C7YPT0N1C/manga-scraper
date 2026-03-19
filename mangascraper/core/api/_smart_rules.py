#!/usr/bin/env python3

import re

def tokenise_smart_expression(expression: str) -> list[str]:
    text = str(expression or "").strip()
    if not text:
        return []

    tokens = re.findall(r"F\d+|AND|OR|NOT|\(|\)", text, flags=re.IGNORECASE)
    normalised = [token.upper() for token in tokens]
    compact_source = re.sub(r"\s+", "", text).upper()
    compact_tokens = "".join(normalised)
    if compact_source != compact_tokens:
        raise ValueError("Expression contains unsupported tokens. Use only F#, AND, OR, NOT, and brackets.")
    return normalised

def smart_expression_to_rpn(expression: str) -> list[str]:
    tokens = tokenise_smart_expression(expression)
    if not tokens:
        raise ValueError("Smart expression is required for smart collections.")

    def _kind(token: str) -> str:
        if re.fullmatch(r"F\d+", token):
            return "operand"
        if token in {"AND", "OR"}:
            return "binary"
        if token == "NOT":
            return "unary"
        if token == "(":
            return "lparen"
        if token == ")":
            return "rparen"
        return "unknown"

    prev_kind = None
    balance = 0
    for token in tokens:
        kind = _kind(token)
        if kind == "unknown":
            raise ValueError(f"Unsupported token '{token}'.")

        if kind == "lparen":
            balance += 1
        elif kind == "rparen":
            balance -= 1
            if balance < 0:
                raise ValueError("Expression has mismatched brackets.")

        if prev_kind is None:
            if kind not in {"operand", "unary", "lparen"}:
                raise ValueError("Expression must start with a filter, NOT, or '(' .")
        elif prev_kind in {"operand", "rparen"}:
            if kind in {"operand", "unary", "lparen"}:
                raise ValueError("Explicit AND is required between filters and groups.")
        elif prev_kind in {"binary", "unary", "lparen"}:
            if kind in {"binary", "rparen"}:
                raise ValueError("Expression has an operator in an invalid position.")

        prev_kind = kind

    if balance != 0:
        raise ValueError("Expression has mismatched brackets.")
    if prev_kind in {"binary", "unary", "lparen"}:
        raise ValueError("Expression cannot end with an operator.")

    precedence = {"NOT": 3, "AND": 2, "OR": 1}
    right_associative = {"NOT"}
    output = []
    stack = []

    for token in tokens:
        if re.fullmatch(r"F\d+", token):
            output.append(token)
            continue
        if token in {"AND", "OR", "NOT"}:
            while stack and stack[-1] in precedence:
                top = stack[-1]
                if top not in precedence:
                    break
                if precedence[top] > precedence[token] or (
                    precedence[top] == precedence[token] and token not in right_associative
                ):
                    output.append(stack.pop())
                else:
                    break
            stack.append(token)
            continue
        if token == "(":
            stack.append(token)
            continue
        if token == ")":
            while stack and stack[-1] != "(":
                output.append(stack.pop())
            if not stack:
                raise ValueError("Expression has mismatched brackets.")
            stack.pop()

    while stack:
        top = stack.pop()
        if top in {"(", ")"}:
            raise ValueError("Expression has mismatched brackets.")
        output.append(top)

    return output

def evaluate_smart_rpn(rpn_tokens: list[str], filter_result_map: dict[str, bool]) -> bool:
    stack = []
    for token in rpn_tokens:
        if re.fullmatch(r"F\d+", token):
            stack.append(bool(filter_result_map.get(token, False)))
            continue
        if token == "NOT":
            if not stack:
                return False
            stack.append(not stack.pop())
            continue
        if token in {"AND", "OR"}:
            if len(stack) < 2:
                return False
            right = bool(stack.pop())
            left = bool(stack.pop())
            stack.append(left and right if token == "AND" else left or right)
    return bool(stack[-1]) if len(stack) == 1 else False
