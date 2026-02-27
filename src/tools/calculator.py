"""计算器工具，支持安全的数学表达式计算。"""

import ast
import operator
from typing import Any, Dict

from src.tools.base_tool import BaseTool

# 允许的安全运算符
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node):
    """安全地计算 AST 节点，只允许数字和基本运算符。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        return _SAFE_OPERATORS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        operand = _safe_eval(node.operand)
        return _SAFE_OPERATORS[op_type](operand)
    else:
        raise ValueError(f"不支持的表达式类型: {type(node).__name__}")


class CalculatorTool(BaseTool):
    """安全的数学计算器。

    支持加减乘除、取模、幂运算，拒绝任意代码执行。
    """

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return (
            "安全的数学计算器，计算数学表达式并返回精确结果。"
            "支持：加(+)、减(-)、乘(*)、除(/)、整除(//)、取模(%)、幂(**)运算。"
            "适用场景：需要精确数值计算时使用，如单位换算、价格计算、统计数据等。"
            "不适用：不支持日期运算、变量、函数调用或编程逻辑，仅支持纯数字表达式。"
            "注意：日期间隔天数请直接推算，不要传入日期格式（如 2026-05-01），会被错误解析为减法。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，例如: '2 + 3 * 4', '(10 - 2) ** 3'",
                }
            },
            "required": ["expression"],
        }

    def execute(self, expression: str, **kwargs) -> str:
        try:
            tree = ast.parse(expression, mode="eval")
            result = _safe_eval(tree)
            return f"{expression} = {result}"
        except ZeroDivisionError:
            return f"计算错误: 除数不能为零"
        except (ValueError, TypeError, SyntaxError) as e:
            return f"计算错误: {e}"
