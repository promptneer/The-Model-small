# -*- coding: utf-8 -*-
"""
原子操作注册表
==============
「鸟语DSL」的最小执行单元 = 原子操作（Atom）：
  - 操作不可再拆分；
  - 要么完整执行成功，要么通过 rollback 回滚到执行前状态；
  - 每个原子操作自带参数 schema（前置校验），非法参数在调度器层被拦截。

调度器只认识注册表里的名字；未注册的操作一律视为「非法指令」。
"""
from __future__ import annotations


class AtomError(Exception):
    """原子操作业务失败（调度器会据此触发 on_error 策略）。"""


class Atom:
    """所有原子操作必须继承本类。"""

    #: 操作名（DSL 中 op 字段的唯一标识，如 "file.find"）
    name: str = ""
    #: 参数 schema（简化 JSON-Schema：type/required/properties/enum/min/max/pattern）
    schema: dict = {"type": "object", "required": [], "properties": {}}

    def run(self, ctx, params) -> dict:
        """执行原子操作，返回结构化结果（必须是 dict/可 JSON 序列化）。"""
        raise NotImplementedError

    def rollback(self, ctx, params, result) -> None:
        """失败时回滚。写操作必须实现；只读操作可省略（默认无操作）。"""
        return None


_REGISTRY: dict[str, Atom] = {}


def register(cls):
    """类装饰器：把原子操作注册进注册表。"""
    if not cls.name:
        raise ValueError("原子操作必须有 name: %s" % cls)
    if cls.name in _REGISTRY:
        raise ValueError("原子操作重复注册: %s" % cls.name)
    _REGISTRY[cls.name] = cls()
    return cls


def get(name):
    return _REGISTRY.get(name)


def has(name):
    return name in _REGISTRY


def names():
    return sorted(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# 参数 schema 校验（轻量实现，不引入第三方库）
# ---------------------------------------------------------------------------
def validate_params(schema, params):
    """按 schema 校验参数。返回 (ok, error_msg)。"""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return False, "参数必须是对象 (dict)"
    for req in schema.get("required", []):
        if req not in params:
            return False, "缺少必填参数: %s" % req
    props = schema.get("properties", {})
    # 宽容层：小模型常把数字写成字符串（如 limit:"50"、depth:"3"），
    # 对声明为 number/integer 的纯数字字符串自动转换，仍严格拦截真正的非法参数。
    for key, spec in props.items():
        if key not in params:
            continue
        t = spec.get("type")
        val = params[key]
        if t in ("number", "integer") and isinstance(val, str):
            s = val.strip()
            try:
                if s.lstrip("+-").isdigit():
                    params[key] = int(s) if t == "integer" else float(s)
            except (ValueError, OverflowError):
                pass
    for key, spec in props.items():
        if key not in params:
            continue
        val = params[key]
        t = spec.get("type")
        if t and not _type_ok(t, val):
            return False, "参数 %s 类型应为 %s，实际 %s" % (key, t, type(val).__name__)
        if t == "string" and "pattern" in spec:
            import re
            if not re.search(spec["pattern"], val):
                return False, "参数 %s 不满足模式 %s" % (key, spec["pattern"])
        if t == "number" and "min" in spec and val < spec["min"]:
            return False, "参数 %s 不能小于 %s" % (key, spec["min"])
        if t == "number" and "max" in spec and val > spec["max"]:
            return False, "参数 %s 不能大于 %s" % (key, spec["max"])
        if "enum" in spec and val not in spec["enum"]:
            return False, "参数 %s 取值必须是 %s 之一" % (key, spec["enum"])
    return True, ""


def _type_ok(t, val):
    if t == "string":
        return isinstance(val, str)
    if t == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if t == "integer":
        return isinstance(val, int) and not isinstance(val, bool)
    if t == "boolean":
        return isinstance(val, bool)
    if t == "array":
        return isinstance(val, list)
    if t == "object":
        return isinstance(val, dict)
    return True
