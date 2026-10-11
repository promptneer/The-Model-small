# -*- coding: utf-8 -*-
"""
端到端自测：不依赖模型，mock 大脑 + 真调度器 + 真台账/文件系统。
验证点：
  1. 合法 DSL 正常执行（ledger.query/stats）
  2. 多步骤执行（find → query）
  3. 写操作 + 后续步骤失败 → 自动回滚（原子性）
  4. 非法 op 拦截（不执行任何步骤）
  5. 非法 JSON 拦截
  6. 前置校验 guard 拦截
用法：python test_run.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import dsl_parser, ledger, scheduler  # noqa: E402
from atoms import (  # noqa: F401,E402
    file_find, ledger_query, ledger_update, system_info,
)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


def main():
    print("=" * 60)
    print("老仓管 · 端到端自测（mock 大脑 / 真调度 / 真台账）")
    print("=" * 60)
    ledger.init_db()

    sched = scheduler.Scheduler()

    # ---- 用例1：台账统计 ----
    print("\n[1] 合法 DSL：ledger.query stats")
    dsl1 = json.dumps({
        "dsl_version": "1.0",
        "intent": "统计台账",
        "steps": [{"op": "ledger.query", "params": {"action": "stats"}}],
    })
    r1 = sched.dispatch(dsl1)
    check("ok=True", r1.ok, r1.error)
    check("total>=0", r1.steps and r1.steps[0].result.get("data", {}).get("total", -1) >= 0)

    # ---- 用例2：文件查找（真实临时目录） ----
    print("\n[2] 合法 DSL：file.find（真实临时目录）")
    tmp = tempfile.mkdtemp(prefix="lc_test_")
    for i in range(3):
        with open(os.path.join(tmp, "报表%d.xlsx" % i), "w") as f:
            f.write("x" * 10)
    with open(os.path.join(tmp, "说明.txt"), "w") as f:
        f.write("y")
    dsl2 = json.dumps({
        "dsl_version": "1.0",
        "intent": "找 xlsx",
        "steps": [{"op": "file.find", "params": {"root": tmp, "pattern": "*.xlsx", "depth": 2}}],
    })
    r2 = sched.dispatch(dsl2)
    check("ok=True", r2.ok, r2.error)
    check("匹配3个xlsx", r2.steps[0].result.get("matched") == 3, str(r2.steps[0].result))

    # ---- 用例3：多步骤 + 写操作回滚（核心原子性验证） ----
    print("\n[3] 原子性：insert 成功后第2步失败 → 自动回滚删除刚插入的记录")
    before = ledger.stats()["total"]
    dsl3 = json.dumps({
        "dsl_version": "1.0",
        "intent": "登记资产并查询不存在路径",
        "steps": [
            {"op": "ledger.update",
             "params": {"action": "insert", "name": "测试回滚资产-临时", "type": "测试",
                        "location": "测试区", "quantity": 1, "unit": "件"}},
            {"op": "file.find", "params": {"root": "Z:/不存在的目录/xyz", "pattern": "*.c"}},
        ],
    })
    r3 = sched.dispatch(dsl3)
    after = ledger.stats()["total"]
    check("整体失败", not r3.ok, r3.error)
    check("第1步曾成功", r3.steps[0].ok)
    check("第1步已回滚", r3.steps[0].rollback_done)
    check("台账总数未变(before==after)", before == after, "%s vs %s" % (before, after))
    rows = ledger.find_asset("测试回滚资产-临时")
    check("临时资产已清除", len(rows) == 0, str(rows))

    # ---- 用例4：非法 op 拦截 ----
    print("\n[4] 非法指令拦截：未注册 op")
    dsl4 = json.dumps({
        "dsl_version": "1.0",
        "intent": "hack",
        "steps": [{"op": "shell.run", "params": {"cmd": "rm -rf /"}}],
    })
    r4 = sched.dispatch(dsl4)
    check("被拦截", not r4.ok and r4.reason == "dsl_parse_failed", r4.error)

    # ---- 用例5：非法 JSON 拦截 ----
    print("\n[5] 非法 JSON 拦截：模型输出散文")
    r5 = sched.dispatch("好的，我来帮你查找文件。首先你需要……")
    check("被拦截", not r5.ok and r5.reason == "dsl_parse_failed", r5.error)

    # ---- 用例6：guard 前置校验 ----
    print("\n[6] guard 拦截：目录不存在")
    dsl6 = json.dumps({
        "dsl_version": "1.0",
        "intent": "找文件",
        "steps": [{"op": "file.find", "params": {"pattern": "*.c"},
                   "guard": {"require_dir": "Z:/no/such/dir"}}],
    })
    r6 = sched.dispatch(dsl6)
    check("guard 拦截", not r6.ok and r6.reason == "guard_failed", r6.error)

    # ---- 用例7：on_error=continue ----
    print("\n[7] on_error=continue：第1步失败不中断第2步")
    dsl7 = json.dumps({
        "dsl_version": "1.0",
        "intent": "容错执行",
        "steps": [
            {"op": "ledger.query", "params": {"action": "find", "keyword": ""},
             "on_error": "continue"},
            {"op": "ledger.query", "params": {"action": "stats"}},
        ],
    })
    r7 = sched.dispatch(dsl7)
    check("整体成功(continue)", r7.ok, r7.error)
    check("第1步失败已记录", len(r7.steps) == 2 and not r7.steps[0].ok and r7.steps[1].ok)

    # ---- 用例8：参数 schema 校验 ----
    print("\n[8] 参数校验：missing required pattern")
    dsl8 = json.dumps({
        "dsl_version": "1.0",
        "intent": "找文件",
        "steps": [{"op": "file.find", "params": {"root": "E:/"}}],
    })
    r8 = sched.dispatch(dsl8)
    check("参数校验拦截", not r8.ok and r8.reason == "guard_failed", r8.error)

    print("\n" + "=" * 60)
    print("结果: %d 通过, %d 失败" % (PASS, FAIL))
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
