# -*- coding: utf-8 -*-
"""
原子操作：ledger.update —— 台账写入（可回滚）
==============================================
写操作三件套：insert / update / delete。
每次成功写入都返回 undo 信息，调度器在后续步骤失败时逆序调用 rollback，
保证「要么完整执行，要么回滚」。
"""
from core import ledger
from core.registry import Atom, AtomError, register


@register
class LedgerUpdate(Atom):
    name = "ledger.update"

    schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action":   {"type": "string", "enum": ["insert", "update", "delete"]},
            "name":     {"type": "string"},
            "type":     {"type": "string"},
            "location": {"type": "string"},
            "quantity": {"type": "number"},
            "unit":     {"type": "string"},
            "owner":    {"type": "string"},
            "note":     {"type": "string"},
            "id":       {"type": "integer", "min": 1},
            "fields":   {"type": "object"},
        },
    }

    def run(self, ctx, params):
        action = params["action"]
        if action == "insert":
            name = params.get("name")
            if not name:
                raise AtomError("insert 需要 name 参数")
            row, undo = ledger.insert_asset(
                name,
                params.get("type", "未知"),
                params.get("location", "未知"),
                params.get("quantity", 1),
                params.get("unit", "件"),
                params.get("owner", ""),
                params.get("note", ""),
            )
            ledger.audit("ledger.update/insert", params, {"row": row}, True)
            return {"op": "ledger.update", "action": "insert", "row": row,
                    "undo": undo, "message": "已登记资产: %s" % name}

        if action == "update":
            aid = params.get("id")
            fields = params.get("fields") or {}
            if not aid:
                raise AtomError("update 需要 id 参数")
            if not fields:
                raise AtomError("update 需要 fields 参数")
            row, undo = ledger.update_asset(aid, fields)
            ledger.audit("ledger.update/update", params, {"row": row}, True)
            return {"op": "ledger.update", "action": "update", "row": row,
                    "undo": undo, "message": "已更新资产 id=%s" % aid}

        if action == "delete":
            aid = params.get("id")
            if not aid:
                raise AtomError("delete 需要 id 参数")
            _, undo = ledger.delete_asset(aid)
            ledger.audit("ledger.update/delete", params, {"id": aid}, True)
            return {"op": "ledger.update", "action": "delete", "id": aid,
                    "undo": undo, "message": "已删除资产 id=%s" % aid}

        raise AtomError("未知写动作: %s" % action)

    def rollback(self, ctx, params, result):
        undo = (result or {}).get("undo")
        if not undo:
            return
        ledger.rollback(undo)
        ledger.audit("rollback/ledger.update", {"undo": undo}, {"ok": True}, True)
        ctx.log("已回滚 %s（undo=%s）" % (params.get("action"), undo.get("action")))
