# -*- coding: utf-8 -*-
"""
「老仓管」交互入口（CLI）
=========================
用法：
    python main.py                 # 自动探测后端（Ollama > mock）
    python main.py --brain mock    # 强制 mock（无模型验证链路）
    python main.py --brain ollama  # 强制 Ollama
    python main.py --brain llama   # 强制 llama.cpp server
    python main.py --brain pyllama # 强制 llama-cpp-python 直读 GGUF（需 LC_GGUF）

交互命令：
    /quit         退出
    /ledger       查看台账全部资产
    /stats        台账统计
    /recent [n]   最近 n 条操作记录（默认 10）
    /dsl on|off   是否展开显示模型产出的 DSL
    /mode         显示当前大脑后端
    /help         帮助

典型需求（直接输入）：
    「看一下仓库现在有多少资产」→ ledger.query/stats
    「查台账里有没有防静电手套」→ ledger.query/find
    「在 E:/ 找所有 .xlsx 文件」→ file.find
    「登记一台新叉车，放维修区，1 辆」→ ledger.update/insert
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import brain, ledger, scheduler  # noqa: E402
from atoms import (  # noqa: F401,E402  (import 副作用：注册原子操作)
    file_find, ledger_query, ledger_update, system_info,
)

C = {
    "reset": "\033[0m", "bold": "\033[1m",
    "green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m",
    "cyan": "\033[96m", "dim": "\033[2m",
}


def p(text, color=None):
    if color and sys.stdout.isatty():
        print(C.get(color, "") + text + C["reset"])
    else:
        print(text)


def print_ledger(rows):
    if not rows:
        p("（台账为空）", "yellow")
        return
    print("")
    print("%-4s %-18s %-8s %-16s %-6s %-4s %-10s" % ("ID", "名称", "类型", "位置", "数量", "单位", "负责人"))
    print("-" * 76)
    for r in rows:
        print("%-4s %-18s %-8s %-16s %-6s %-4s %-10s" % (
            r["id"], r["name"], r["type"], r["location"], r["quantity"], r["unit"], r["owner"] or ""))


def main():
    force = None
    if len(sys.argv) > 1 and sys.argv[1].startswith("--brain="):
        force = sys.argv[1].split("=", 1)[1]
    elif len(sys.argv) > 2 and sys.argv[1] == "--brain":
        force = sys.argv[2]

    ledger.init_db()
    b = brain.create_brain(force)
    sched = scheduler.Scheduler()
    show_dsl = True

    p("=" * 62, "cyan")
    p("  老仓管 · 轻量本地仓库智能体  (模型只做大脑，脚本负责体力)", "bold")
    p("  大脑后端: %s" % b.name, "cyan")
    p("  台账: %s" % ledger.DB_PATH, "dim")
    p("  输入需求开始对话；输入 /help 查看命令。", "dim")
    p("=" * 62, "cyan")

    while True:
        try:
            text = input("\n\033[93m仓管需求\033[0m> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        if not text:
            continue

        if text.startswith("/"):
            cmd = text[1:].strip().split()
            c0 = cmd[0] if cmd else ""
            if c0 == "quit":
                break
            elif c0 == "ledger":
                print_ledger(ledger.list_all())
            elif c0 == "stats":
                st = ledger.stats()
                print("资产总数: %s" % st["total"])
                for r in st["by_type"]:
                    print("  - %s: %s" % (r["type"], r["cnt"]))
            elif c0 == "recent":
                n = int(cmd[1]) if len(cmd) > 1 else 10
                for r in ledger.recent_audit(n):
                    p("%s [%s] %s %s" % (r["ts"], "OK" if r["ok"] else "FAIL",
                                          r["op"], json.dumps(r["params"], ensure_ascii=False)[:60]), "dim")
            elif c0 == "dsl":
                show_dsl = cmd[1] != "off" if len(cmd) > 1 else True
                p("DSL 展开: %s" % ("开" if show_dsl else "关"), "cyan")
            elif c0 == "mode":
                p("大脑后端: %s" % b.name, "cyan")
            else:
                print("/help  quit ledger stats recent [n]  dsl on|off  mode")
            continue

        # ---- 完整链路：需求 → 大脑(DSL) → 调度器(执行) → 汇报 ----
        p("…大脑思考中（模型输出 DSL）", "dim")
        try:
            model_out = b.chat(text)
        except Exception as e:
            p("大脑调用失败: %s" % e, "red")
            continue

        if show_dsl:
            p("┌─ 鸟语DSL（模型产出）", "dim")
            try:
                pretty = json.dumps(json.loads(brain_parser_extract(model_out)), ensure_ascii=False, indent=2)
                for line in pretty.splitlines():
                    p("│ " + line, "dim")
            except Exception:
                for line in model_out.strip().splitlines():
                    p("│ " + line, "dim")
            p("└─" + "─" * 40, "dim")

        sr = sched.dispatch(model_out)
        if not sr.ok:
            if sr.reason == "dsl_parse_failed":
                p("✗ 模型输出的不是合法鸟语DSL（已拦截，未执行任何操作）", "red")
                p("  模型原文: %s" % model_out.strip()[:160], "dim")
            else:
                p("✗ 执行失败: %s" % sr.error, "red")
                for s in sr.steps:
                    if not s.ok:
                        p("  - step%d %s: %s" % (s.index + 1, s.op, s.error), "dim")
                        if s.rollback_done:
                            p("    ↻ 已回滚", "yellow")
            continue

        # 汇报
        p("✓ 执行完成 %d 步" % len(sr.steps), "green")
        summary = None
        if b.name in ("ollama", "llama", "pyllama"):
            try:
                summary = b.summarize(text, sr.to_dict())
            except Exception:
                summary = None
        if summary:
            p("「老仓管」: " + summary, "green")
        else:
            # 默认格式化汇报（不依赖模型）
            lines = []
            for s in sr.steps:
                r = s.result
                op = s.op
                if op == "file.find":
                    lines.append("找到 %d 个匹配文件（共 %d 个，显示前 %d）："
                                 % (r.get("matched", 0), r.get("total", 0), len(r.get("files", []))))
                    for f in r.get("files", [])[:8]:
                        lines.append("  · %s (%d B)" % (f["name"], f["size"]))
                elif op == "ledger.query":
                    act = r.get("action")
                    if act == "stats":
                        lines.append("台账共 %s 项资产：%s"
                                     % (r["data"]["total"],
                                        ", ".join("%s×%s" % (x["type"], x["cnt"]) for x in r["data"]["by_type"])))
                    elif act in ("find", "list_type", "list_all"):
                        lines.append("查到 %d 条：" % r.get("matched", 0))
                        for row in r.get("rows", [])[:8]:
                            lines.append("  · [%s] %s | %s | %s×%s%s"
                                         % (row.get("id"), row.get("name"), row.get("location"),
                                            row.get("quantity"), row.get("unit"),
                                            " | " + row.get("owner", "") if row.get("owner") else ""))
                    elif act == "recent":
                        lines.append("最近 %d 条操作：" % len(r.get("rows", [])))
                elif op == "ledger.update":
                    lines.append(r.get("message", "台账已更新"))
                elif op == "system.info":
                    g = r.get("gpu") or {}
                    lines.append("GPU: %s %s | 内存: %s GB | 磁盘剩余: %s GB | Python %s"
                                 % (g.get("name", "未知"), g.get("vram", ""),
                                    r.get("ram", {}).get("free_gb", "?"),
                                    r.get("disk", {}).get("free_gb", "?"), r.get("python")))
            p("「老仓管」: " + " ".join(lines) if lines else "（无输出）", "green")


def brain_parser_extract(text):
    """显示用：容错提取 JSON（与 dsl_parser 同逻辑的轻量版）。"""
    import re
    t = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        t = m.group(1).strip()
    return t[t.find("{"): t.rfind("}") + 1]


if __name__ == "__main__":
    main()
