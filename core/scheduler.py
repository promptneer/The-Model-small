# -*- coding: utf-8 -*-
"""
调度器：DSL → 原子操作执行引擎
==============================
完整链路第③环。职责：
  1. 解析 DSL（非法指令 → 拦截，不执行任何原子操作）；
  2. 逐步骤执行：前置校验(guard) → 参数校验(schema) → 原子操作.run；
  3. 失败策略：
       - on_error=abort：逆序回滚已成功步骤（写操作），整单失败；
       - on_error=continue：记录失败，继续后续步骤；
  4. 收集执行轨迹与结果，供大脑整理反馈。

原子性保证：写操作原子（ledger.update）自带回滚信息（undo）；
调度器只负责在合适时机统一调用 rollback。读操作天然无副作用。
"""
import logging
import os
from dataclasses import dataclass, field
from typing import List

from . import dsl_parser
from .registry import get, has, validate_params
from .dsl_parser import DSLParseError

log = logging.getLogger("scheduler")


@dataclass
class StepResult:
    index: int
    op: str
    ok: bool
    result: dict = field(default_factory=dict)
    error: str = ""
    rollback_done: bool = False


class GuardError(Exception):
    pass


def _run_guard(guard, params):
    """前置校验。guard 支持：
       {"require_dir": "路径"}     路径必须存在且是目录
       {"require_file": "路径"}    路径必须存在且是文件
       {"require_key": "字段名"}   params 中该字段非空
    """
    for key, value in guard.items():
        if key == "require_dir":
            if not os.path.isdir(value):
                raise GuardError("前置校验失败：目录不存在 %s" % value)
        elif key == "require_file":
            if not os.path.isfile(value):
                raise GuardError("前置校验失败：文件不存在 %s" % value)
        elif key == "require_key":
            if value not in params or params[value] in (None, "", []):
                raise GuardError("前置校验失败：缺少参数 %s" % value)
        else:
            raise GuardError("前置校验类型未知: %s" % key)


@dataclass
class ScheduleResult:
    ok: bool
    intent: str
    steps: List[StepResult] = field(default_factory=list)
    error: str = ""
    reason: str = ""

    def to_dict(self):
        return {
            "ok": self.ok,
            "intent": self.intent,
            "reason": self.reason,
            "error": self.error,
            "steps": [
                {"index": s.index, "op": s.op, "ok": s.ok,
                 "result": s.result, "error": s.error,
                 "rollback_done": s.rollback_done}
                for s in self.steps
            ],
        }


class Scheduler:
    def __init__(self, max_steps=10):
        self.max_steps = max_steps

    # ------------------------------------------------------------------
    def dispatch(self, model_output: str) -> ScheduleResult:
        """
        主入口：接收模型原始输出（应包含鸟语DSL JSON）。
        任何解析/校验/执行失败都会得到结构化的 ScheduleResult。
        """
        # 1) 解析：非法指令在此拦截
        try:
            dsl = dsl_parser.parse(model_output)
        except DSLParseError as e:
            return ScheduleResult(ok=False, intent="", error=str(e),
                                  reason="dsl_parse_failed")

        return self.execute(dsl)

    # ------------------------------------------------------------------
    def execute(self, dsl: dict) -> ScheduleResult:
        intent = dsl.get("intent", "")
        result = ScheduleResult(ok=True, intent=intent)
        done: List[StepResult] = []  # 已成功步骤（供回滚）

        for i, step in enumerate(dsl["steps"]):
            op_name = step["op"]
            params = step.get("params", {})
            sr = StepResult(index=i, op=op_name, ok=False)

            # 2) 操作白名单（解析器已拦一道，这里双保险）
            if not has(op_name):
                sr.error = "未注册原子操作: %s" % op_name
                result.steps.append(sr)
                result.ok = False
                result.error = sr.error
                result.reason = "unknown_op"
                break

            atom = get(op_name)
            try:
                # 3) 前置校验
                if step.get("guard"):
                    _run_guard(step["guard"], params)
                # 4) 参数 schema 校验
                ok, err = validate_params(atom.schema, params)
                if not ok:
                    raise GuardError("参数校验失败: %s" % err)
                # 5) 执行原子操作
                ctx = _Ctx(self)
                out = atom.run(ctx, params)
                sr.ok = True
                sr.result = out if isinstance(out, dict) else {"value": out}
                result.steps.append(sr)
                done.append(sr)
            except GuardError as e:
                sr.error = str(e)
                result.steps.append(sr)
                result.ok = False
                result.error = str(e)
                result.reason = "guard_failed"
                break
            except DSLParseError as e:
                sr.error = str(e)
                result.steps.append(sr)
                result.ok = False
                result.error = str(e)
                result.reason = "dsl_error"
                break
            except Exception as e:  # 原子操作业务失败
                sr.error = "执行失败: %s" % e
                result.steps.append(sr)
                # 失败策略
                if step.get("on_error") == "continue":
                    continue
                result.ok = False
                result.error = sr.error
                result.reason = "step_failed"
                # 6) 逆序回滚
                rollback_errors = self._rollback(done)
                if rollback_errors:
                    result.error += "；回滚异常: %s" % "; ".join(rollback_errors)
                break

        return result

    # ------------------------------------------------------------------
    def _rollback(self, done: List[StepResult]) -> List[str]:
        """逆序回滚全部已成功步骤中「带 undo 语义」的写操作。"""
        errors = []
        for sr in reversed(done):
            atom = get(sr.op)
            if not atom:
                continue
            try:
                atom.rollback(_Ctx(self), {"action": "rollback"}, sr.result)
                sr.rollback_done = True
            except Exception as e:
                errors.append("%s: %s" % (sr.op, e))
        return errors


class _Ctx:
    """原子操作执行上下文。"""

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def log(self, msg):
        log.info("[ctx] %s", msg)
