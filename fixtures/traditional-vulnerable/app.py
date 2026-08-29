"""Deliberately vulnerable local-only fixture used by adapter acceptance tests."""


def unsafe_expression(user_expression: str):
    return eval(user_expression)
