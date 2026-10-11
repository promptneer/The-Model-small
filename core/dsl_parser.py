# -*- coding: utf-8 -*-
"""
鸟语DSL 解析器
==============
职责：把「模型输出的原始文本」解析成经过校验的标准 DSL 对象。
模型输出常常夹带 markdown 代码块、散文说明——本模块负责容错提取，
并在调度器执行前完成结构校验，非法指令在此即被拦截。

合法 DSL（标准 JSON）：
{
  "dsl_version": "1.0",
  "intent": "一句话的人类意图（给调度器和日志看）",
  "steps": [
    {
      "op": "file.find",
      "params": {"pattern": "*.xlsx", "root": "E:/warehouse", "depth": 3},
      "guard": {"require_dir": "E:/warehouse"},   // 可选：前置校验
      "on_error": "abort",                        // 可选：abort(默认) | continue
      "undo": true                                // 可选：失败时是否回滚（写操作默认 true）
    }
  ]
}
"""
import json
import re

VALID_OPS = {"file.find", "ledger.query", "ledger.update", "system.info"}
ON_ERROR_CHOICES = {"abort", "continue"}


class DSLParseError(Exception):
    """DSL 非法。调度器应把该错误返回给大脑，不执行任何原子操作。"""


def extract_json(raw_text):
    """从模型输出中容错提取 JSON 对象字符串。"""
    if not raw_text or not isinstance(raw_text, str):
        raise DSLParseError("模型输出为空")
    text = raw_text.strip()
    # 剥掉 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise DSLParseError("输出中未找到 JSON 对象: %s" % text[:120])
    return text[start:end + 1]


def parse(raw_text):
    """
    解析并校验模型输出为标准 DSL。
    返回 {dsl_version, intent, steps:[...]}；非法则抛 DSLParseError。
    """
    body = extract_json(raw_text)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        raise DSLParseError("JSON 解析失败: %s" % e)

    if not isinstance(obj, dict):
        raise DSLParseError("DSL 顶层必须是对象")
    dsl_version = obj.get("dsl_version", "1.0")
    intent = str(obj.get("intent", "")).strip()
    steps = obj.get("steps")
    if not isinstance(steps, list) or not steps:
        raise DSLParseError("DSL 缺少非空 steps 数组")
    if len(steps) > 10:
        raise DSLParseError("steps 数量超限（最多 10 步）")

    cleaned = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise DSLParseError("steps[%d] 必须是对象" % i)
        op = step.get("op")
        if not isinstance(op, str) or op not in VALID_OPS:
            raise DSLParseError("steps[%d] 引用了未注册操作: %r（合法: %s）"
                                % (i, op, ", ".join(sorted(VALID_OPS))))
        params = step.get("params", {})
        if not isinstance(params, dict):
            raise DSLParseError("steps[%d] 的 params 必须是对象" % i)
        on_error = step.get("on_error", "abort")
        if on_error not in ON_ERROR_CHOICES:
            raise DSLParseError("steps[%d] 的 on_error 非法: %r" % (i, on_error))
        guard = step.get("guard", {})
        if guard and not isinstance(guard, dict):
            raise DSLParseError("steps[%d] 的 guard 必须是对象" % i)
        cleaned.append({
            "op": op,
            "params": params,
            "guard": guard or {},
            "on_error": on_error,
            "undo": bool(step.get("undo", True)),
        })

    return {"dsl_version": dsl_version, "intent": intent, "steps": cleaned}
