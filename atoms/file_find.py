# -*- coding: utf-8 -*-
"""
原子操作：file.find —— 文件查找
================================
负责「遍历文件、匹配、汇总」这类枯燥连续操作。
模型不参与：只把用户的模糊意图翻译成 {root, pattern, keyword} 即可。
"""
import glob
import os
import time

from core.registry import Atom, AtomError, register


@register
class FileFind(Atom):
    name = "file.find"

    schema = {
        "type": "object",
        "required": ["pattern"],
        "properties": {
            "pattern": {"type": "string"},
            "root":    {"type": "string"},
            "keyword": {"type": "string"},
            "depth":   {"type": "integer", "min": 0, "max": 20},
            "limit":   {"type": "integer", "min": 1, "max": 200},
        },
    }

    def run(self, ctx, params):
        root = params.get("root") or os.path.expanduser("~")
        pattern = params["pattern"]
        depth = params.get("depth", 5)
        limit = params.get("limit", 50)
        keyword = params.get("keyword")

        if not os.path.isdir(root):
            raise AtomError("目录不存在: %s" % root)

        results = []
        root = os.path.abspath(root)
        base_depth = root.rstrip(os.sep).count(os.sep)

        def walk(d):
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                return
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if d.rstrip(os.sep).count(os.sep) - base_depth < depth:
                            walk(e.path)
                    elif e.is_file(follow_symlinks=False):
                        if glob.fnmatch.fnmatch(e.name, pattern):
                            if keyword and keyword.lower() not in e.name.lower():
                                continue
                            st = e.stat()
                            results.append({
                                "path": e.path,
                                "name": e.name,
                                "size": st.st_size,
                                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                            })
                except OSError:
                    continue

        walk(root)
        results.sort(key=lambda r: r["mtime"], reverse=True)
        total = len(results)
        results = results[:limit]
        return {
            "op": "file.find",
            "root": root,
            "pattern": pattern,
            "keyword": keyword or "",
            "matched": len(results),
            "total": total,
            "truncated": total > limit,
            "files": results,
        }
