# -*- coding: utf-8 -*-
"""
台账初始化：写入示例仓库资产（可反复执行，幂等）。
仓库场景示例：设备、耗材、文档、工具，带位置与业务规则。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import ledger  # noqa: E402


SAMPLE = [
    # name, type, location, quantity, unit, owner, note
    ("主服务器-01",   "设备", "机房A-01柜",  1, "台", "运维组", "核心业务服务器，禁止关机"),
    ("主服务器-02",   "设备", "机房A-02柜",  1, "台", "运维组", "备用机，每月开机自检一次"),
    ("UPS-01",        "设备", "机房B-电源区", 1, "台", "电工组", "断电后续航 40 分钟"),
    ("千兆交换机-01", "设备", "机房A-网络柜", 2, "台", "网络组", "上行接核心路由器"),
    ("仓库摄像头-01", "设备", "仓库东墙",    4, "个", "安防组", "覆盖入库区与货架区"),
    ("叉车-01",       "工具", "维修区",      1, "辆", "作业组", "每日下班前充满电"),
    ("货架-3层",      "设施", "A区-第3排",  12, "组", "仓储组", "承重上限 500kg/层"),
    ("防静电手套",    "耗材", "工具间-02柜", 50, "双", "作业组", "低于 10 双时需补货"),
    ("打包胶带",      "耗材", "工具间-01柜", 30, "卷", "作业组", "低于 5 卷时需补货"),
    ("入库单-2025",   "文档", "档案室-02架", 200, "份", "文控组", "纸质归档，按月份排序"),
    ("《仓库作业规范》", "文档", "档案室-01架", 3, "本", "文控组", "v2.1 版本，修订需审批"),
    ("温湿度记录仪",  "设备", "仓库中厅",     2, "台", "仓储组", "每 2 小时自动记录一次"),
]

RULES = [
    (1,  "主服务器-01 每周日凌晨 2 点例行维护，需提前报备", 5),
    (5,  "摄像头覆盖区域每周巡检一次，记录在案", 3),
    (8,  "防静电手套数量低于 10 双必须当日补货", 5),
    (11, "《仓库作业规范》只允许文控组借出，借出需登记", 5),
]


def main():
    ledger.init_db()
    added = 0
    for name, type_, location, quantity, unit, owner, note in SAMPLE:
        try:
            ledger.insert_asset(name, type_, location, quantity, unit, owner, note)
            added += 1
        except ValueError:
            pass  # 已存在，幂等跳过
    # 规则：先清空重建（幂等）
    conn = ledger._conn()
    try:
        conn.execute("DELETE FROM rules")
        for asset_id, rule, prio in RULES:
            conn.execute("INSERT INTO rules(asset_id, rule_text, priority) VALUES(?,?,?)",
                         (asset_id, rule, prio))
        conn.commit()
    finally:
        conn.close()
    print("台账初始化完成：新增资产 %d 条，规则 %d 条，数据库：%s"
          % (added, len(RULES), ledger.DB_PATH))


if __name__ == "__main__":
    main()
