# -*- coding: utf-8 -*-
"""
台账模块（知识库/仓库台账）
============================
职责边界（与模型严格隔离）：
  - 台账的读写、检索、统计全部由脚本/本模块完成；
  - 模型永远不直接接触本模块，只通过「鸟语DSL → 调度器 → 原子操作」间接触发；
  - 本模块提供的是「安全的、可审计的」查询与更新接口。

存储：SQLite（data/ledger.db），零第三方依赖。
"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "ledger.db")

# 查询白名单：脚本只能查这些表，杜绝任意 SQL。
TABLE_WHITELIST = {"assets", "rules", "audit"}

_lock = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建表（幂等）。"""
    with _lock:
        conn = _conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL UNIQUE,
                    type       TEXT NOT NULL DEFAULT '未知',
                    location   TEXT NOT NULL DEFAULT '未知',
                    quantity   REAL NOT NULL DEFAULT 1,
                    unit       TEXT NOT NULL DEFAULT '件',
                    owner      TEXT DEFAULT '',
                    note       TEXT DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rules (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id   INTEGER NOT NULL REFERENCES assets(id),
                    rule_text  TEXT NOT NULL,
                    priority   INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT NOT NULL,
                    op          TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    ok          INTEGER NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 查询接口（只读，供原子操作调用）
# ---------------------------------------------------------------------------
def query(sql, params=()):
    """
    白名单查询：仅允许 SELECT，且 FROM/JOIN 引用的表必须在白名单内。
    非法 SQL 直接抛 ValueError（调度器会拦截并转为失败结果，不落库）。
    """
    sql = sql.strip()
    if not sql.lower().startswith("select"):
        raise ValueError("台账只允许 SELECT 查询")
    low = sql.lower()
    for kw in ("insert", "update", "delete", "drop", "alter", "create", "pragma", "attach"):
        if re.search(r"\b%s\b" % kw, low):
            raise ValueError("非法关键字: %s" % kw)
    if ";" in low.rstrip(";"):
        raise ValueError("禁止多语句 SQL")
    # 表名白名单：提取 FROM/JOIN 后的标识符逐一校验
    tables = re.findall(r"\bfrom\s+([a-z_]\w*)", low) + re.findall(r"\bjoin\s+([a-z_]\w*)", low)
    for t in tables:
        if t not in TABLE_WHITELIST:
            raise ValueError("引用了非白名单表: %s" % t)
    with _lock:
        conn = _conn()
        try:
            cur = conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            return rows
        finally:
            conn.close()


def find_asset(keyword):
    """按名称模糊查资产。"""
    return query(
        "SELECT id, name, type, location, quantity, unit, owner, note FROM assets WHERE name LIKE ? ORDER BY id",
        ("%" + keyword + "%",),
    )


def list_by_type(atype):
    return query(
        "SELECT id, name, type, location, quantity, unit, owner FROM assets WHERE type = ? ORDER BY id",
        (atype,),
    )


def list_all():
    return query("SELECT id, name, type, location, quantity, unit, owner, note FROM assets ORDER BY id")


def stats():
    """资产统计：总数、种类数、各类型数量。"""
    rows = query("SELECT type, COUNT(*) AS cnt FROM assets GROUP BY type ORDER BY cnt DESC")
    total = query("SELECT COUNT(*) AS c FROM assets")[0]["c"]
    return {"total": total, "by_type": rows}


def recent_audit(n=10):
    rows = query("SELECT ts, op, params_json, ok FROM audit ORDER BY id DESC LIMIT ?", (n,))
    out = []
    for r in rows:
        try:
            params = json.loads(r["params_json"])
        except Exception:
            params = {}
        out.append({"ts": r["ts"], "op": r["op"], "params": params, "ok": bool(r["ok"])})
    return out


# ---------------------------------------------------------------------------
# 写接口（仅由 ledger_update 原子操作调用；每次写都记审计 + 返回 undo 信息）
# ---------------------------------------------------------------------------
def audit(op, params, result, ok):
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO audit(ts, op, params_json, result_json, ok) VALUES(?,?,?,?,?)",
                (_now(), op, json.dumps(params, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False, default=str), 1 if ok else 0),
            )
            conn.commit()
        finally:
            conn.close()


def insert_asset(name, type_, location, quantity, unit, owner, note):
    """
    新增资产。返回 (row, undo)：
      undo = {"action": "delete", "id": 新id}  用于失败时回滚。
    """
    with _lock:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT INTO assets(name, type, location, quantity, unit, owner, note, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (name, type_, location, quantity, unit, owner, note, _now()),
            )
            conn.commit()
            row = {"id": cur.lastrowid, "name": name, "type": type_, "location": location,
                   "quantity": quantity, "unit": unit, "owner": owner, "note": note}
            return row, {"action": "delete", "id": cur.lastrowid}
        except sqlite3.IntegrityError:
            raise ValueError("资产名已存在: %s" % name)
        finally:
            conn.close()


def update_asset(aid, fields):
    """
    按 id 更新字段（fields 为字典，键必须在白名单内）。
    返回 (row, undo)：undo = {"action": "restore", "id": aid, "old": {旧值}}。
    """
    allowed = {"name", "type", "location", "quantity", "unit", "owner", "note"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError("非法字段: %s" % sorted(bad))
    with _lock:
        conn = _conn()
        try:
            old = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
            if old is None:
                raise ValueError("资产不存在: id=%s" % aid)
            old = dict(old)
            sets = ", ".join("%s=?" % k for k in fields)
            vals = list(fields.values()) + [_now(), aid]
            conn.execute("UPDATE assets SET %s, updated_at=? WHERE id=?" % sets, vals)
            conn.commit()
            row = dict(conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone())
            undo = {"action": "restore", "id": aid,
                    "old": {k: old[k] for k in fields if old[k] != fields[k]}}
            return row, undo
        finally:
            conn.close()


def delete_asset(aid):
    """按 id 删除。返回 (None, undo)，undo 含完整旧行用于恢复。"""
    with _lock:
        conn = _conn()
        try:
            old = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
            if old is None:
                raise ValueError("资产不存在: id=%s" % aid)
            old = dict(old)
            conn.execute("DELETE FROM assets WHERE id=?", (aid,))
            conn.commit()
            undo = {"action": "reinsert", "row": old}
            return None, undo
        finally:
            conn.close()


# 回滚接口：调度器在步骤失败时调用，保证原子性。
def rollback(undo):
    if not undo:
        return
    action = undo.get("action")
    with _lock:
        conn = _conn()
        try:
            if action == "delete":
                conn.execute("DELETE FROM assets WHERE id=?", (undo["id"],))
            elif action == "restore":
                conn.execute(
                    "UPDATE assets SET name=?, type=?, location=?, quantity=?, unit=?, owner=?, note=?, updated_at=? WHERE id=?",
                    (undo["old"]["name"], undo["old"].get("type", "未知"),
                     undo["old"].get("location", "未知"), undo["old"].get("quantity", 1),
                     undo["old"].get("unit", "件"), undo["old"].get("owner", ""),
                     undo["old"].get("note", ""), _now(), undo["id"]),
                )
            elif action == "reinsert":
                r = undo["row"]
                conn.execute(
                    "INSERT INTO assets(id, name, type, location, quantity, unit, owner, note, updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (r["id"], r["name"], r["type"], r["location"], r["quantity"],
                     r["unit"], r["owner"], r["note"], _now()),
                )
            conn.commit()
        finally:
            conn.close()
