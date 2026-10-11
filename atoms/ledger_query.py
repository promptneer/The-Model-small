# -*- coding: utf-8 -*-
"""
原子操作：ledger.query —— 台账查询
==================================
模型不扫描台账，只选择查询动作；实际 SQL 与检索全部在此完成。
只读操作，无回滚需求。
"""
from core import ledger
from core.registry import Atom, AtomError, register


@register
class LedgerQuery(Atom):
    name = "ledger.query"

    schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["find", "list_type", "list_all", "stats", "recent"]},
            "keyword": {"type": "string"},
            "type":   {"type": "string"},
            "limit":  {"type": "integer", "min": 1, "max": 100},
        },
    }

    def run(self, ctx, params):
        action = params["action"]
        if action == "find":
            kw = params.get("keyword") or ""
            if not kw:
                raise AtomError("find 动作需要 keyword 参数")
            rows = ledger.find_asset(kw)
            return {"op": "ledger.query", "action": "find", "keyword": kw,
                    "matched": len(rows), "rows": rows}
        if action == "list_type":
            rows = ledger.list_by_type(params.get("type") or "")
            return {"op": "ledger.query", "action": "list_type",
                    "type": params.get("type"), "matched": len(rows), "rows": rows}
        if action == "list_all":
            rows = ledger.list_all()
            return {"op": "ledger.query", "action": "list_all",
                    "matched": len(rows), "rows": rows}
        if action == "stats":
            return {"op": "ledger.query", "action": "stats", "data": ledger.stats()}
        if action == "recent":
            rows = ledger.recent_audit(params.get("limit", 10))
            return {"op": "ledger.query", "action": "recent",
                    "matched": len(rows), "rows": rows}
        raise AtomError("未知查询动作: %s" % action)
