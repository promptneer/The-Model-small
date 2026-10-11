# -*- coding: utf-8 -*-
"""
原子操作：system.info —— 本机环境信息
=====================================
供模型判断运行环境（GPU / 磁盘 / 显存），也用于部署自检。
只读操作，无回滚。
"""
import ctypes
import os
import shutil
import subprocess

from core.registry import Atom, register


def _gpu_info():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            line = out.stdout.strip().split("\n")[0]
            name, mem, drv = [x.strip() for x in line.split(",")]
            return {"name": name, "vram": mem, "driver": drv}
    except Exception:
        pass
    return None


def _disk():
    try:
        total, used, free = shutil.disk_usage(os.path.abspath(os.sep))
        return {
            "total_gb": round(total / 2**30, 1),
            "free_gb": round(free / 2**30, 1),
        }
    except Exception:
        return None


def _ram():
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {"total_gb": round(m.ullTotalPhys / 2**30, 1),
                "free_gb": round(m.ullAvailPhys / 2**30, 1)}
    except Exception:
        return None


@register
class SystemInfo(Atom):
    name = "system.info"
    schema = {"type": "object", "required": [], "properties": {}}

    def run(self, ctx, params):
        return {
            "op": "system.info",
            "os": "Windows",
            "python": __import__("sys").version.split()[0],
            "gpu": _gpu_info(),
            "ram": _ram(),
            "disk": _disk(),
        }
