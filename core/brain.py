# -*- coding: utf-8 -*-
"""
模型接入层（大脑）
==================
职责（严格边界）：
  - 模型只做「语义理解 → 输出鸟语DSL」，绝不执行任何操作；
  - 执行交给调度器与原子脚本；
  - 后端可插拔：
      LC_BRAIN=ollama  -> 本地 Ollama（推荐：qwen2.5:3b / qwen2.5:1.5b）
      LC_BRAIN=llama   -> llama.cpp llama-server（OpenAI 兼容 /v1/chat/completions）
      LC_BRAIN=mock    -> 内置规则转换器（无 GPU/模型时验证全链路用，明确标注 mock）
  自动探测顺序：环境变量 LC_BRAIN > Ollama 可用 > mock。
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

OLLAMA_URL = os.environ.get("LC_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("LC_MODEL", "qwen2.5:3b")
LLAMA_URL = os.environ.get("LC_LLAMA_URL", "http://127.0.0.1:8080")

SYSTEM_PROMPT = """你是「老仓管」仓库智能体的语言中枢。你只做三件事：
1) 理解用户的自然语言需求；
2) 判断任务目标与执行顺序；
3) 输出「鸟语DSL」——严格 JSON，禁止任何多余文字、解释或代码块标记。
你绝不亲自执行任何操作：遍历文件、读写台账、循环检索全部由调度器按你的 DSL 调用脚本完成。

可用原子操作（op）：
- file.find    在指定目录按模式找文件。params: pattern(必填, 如 *.xlsx/*.c), root(目录), keyword(文件名含关键字), depth(最大深度,默认5), limit(最多条数,默认50)
- ledger.query 查询仓库台账。params: action(find|list_type|list_all|stats|recent), keyword(名称关键词), type(资产类型), limit(默认10)
- ledger.update 写入台账。params: action(insert|update|delete), name, type, location, quantity, unit, owner, note, id, fields
- system.info 查看本机环境(GPU/内存/磁盘/Python)。无参数。

输出格式（严格遵守，只输出这一个 JSON 对象，不要 ``` 包裹）：
{"dsl_version":"1.0","intent":"<一句话意图>","steps":[{"op":"<操作名>","params":{...},"on_error":"abort"}]}

规则：
- 一个需求可以拆成多个 steps 顺序执行；
- 用户要求登记/修改/删除台账时，正常输出 ledger.update；
- 台账里查不到的数据不要编造，改用 ledger.query 查；
- 用户需求模糊无法转成操作时，输出 {"dsl_version":"1.0","intent":"无法理解的需求","steps":[]}。"""


# ===========================================================================
# 后端抽象
# ===========================================================================
class BrainBackend:
    name = "base"

    def chat(self, user_text: str) -> str:
        """返回模型原始输出（应当包含鸟语DSL JSON）。"""
        raise NotImplementedError

    def summarize(self, user_text: str, schedule_result: dict) -> str:
        """执行完成后，把结果整理成人话（可选能力）。返回 None 表示用默认格式。"""
        return None


# ---------------------------------------------------------------------------
# Ollama 后端
# ---------------------------------------------------------------------------
class OllamaBackend(BrainBackend):
    name = "ollama"

    def __init__(self, url=OLLAMA_URL, model=OLLAMA_MODEL):
        self.url = url.rstrip("/")
        self.model = model

    def _post(self, path, payload):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat(self, user_text):
        out = self._post("/api/chat", {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "options": {"temperature": 0.1},
        })
        return out["message"]["content"]

    def summarize(self, user_text, schedule_result):
        try:
            compact = json.dumps(schedule_result, ensure_ascii=False)[:3000]
            out = self._post("/api/chat", {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content":
                     "你是「老仓管」的汇报员。根据执行结果，用 1-3 句中文向仓库管理员汇报：做了什么、结果如何、有无异常。不要编造结果里没有的数字。"},
                    {"role": "user", "content": "需求：%s\n执行结果：%s" % (user_text, compact)},
                ],
                "options": {"temperature": 0.2},
            })
            return out["message"]["content"].strip()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# llama.cpp 后端（OpenAI 兼容）
# ---------------------------------------------------------------------------
class LlamaBackend(BrainBackend):
    name = "llama"

    def __init__(self, url=LLAMA_URL, model="qwen"):
        self.url = url.rstrip("/")
        self.model = model

    def _post(self, path, payload):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat(self, user_text):
        out = self._post("/v1/chat/completions", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        })
        return out["choices"][0]["message"]["content"]

    def summarize(self, user_text, schedule_result):
        try:
            compact = json.dumps(schedule_result, ensure_ascii=False)[:3000]
            out = self._post("/v1/chat/completions", {
                "model": self.model,
                "messages": [
                    {"role": "system", "content":
                     "你是「老仓管」的汇报员。根据执行结果，用 1-3 句中文汇报：做了什么、结果如何、有无异常。不要编造。"},
                    {"role": "user", "content": "需求：%s\n执行结果：%s" % (user_text, compact)},
                ],
                "temperature": 0.2,
                "max_tokens": 512,
            })
            return out["choices"][0]["message"]["content"].strip()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# pyllama 后端：llama-cpp-python 直读 GGUF（本机实测路线）
#   环境变量：LC_GGUF=模型路径（如 E:\mayue\_models\qwen2.5-1.5b-instruct-q4_k_m.gguf）
# ---------------------------------------------------------------------------
class PyLlamaBackend(BrainBackend):
    name = "pyllama"

    def __init__(self, model_path=None, n_ctx=4096, n_gpu_layers=0):
        # CPU 版 llama-cpp-python 无需 CUDA dll；若系统残留无效的 CUDA_PATH 环境变量
        # （指向不存在的 Toolkit 目录）会导致加载失败，这里统一摘除。
        os.environ.pop("CUDA_PATH", None)
        import llama_cpp  # 懒加载：无模型环境不引入依赖
        self.model_path = model_path or os.environ.get("LC_GGUF", "")
        if not self.model_path or not os.path.exists(self.model_path):
            raise RuntimeError("LC_GGUF 未设置或文件不存在: %s" % self.model_path)
        self.llm = llama_cpp.Llama(
            model_path=self.model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,   # RTX5060 可设大值（如 99）启用 GPU；CPU 用 0
            n_threads=os.cpu_count() or 4,
            verbose=False,
        )

    def _chat(self, messages, temperature, max_tokens):
        out = self.llm.create_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return out["choices"][0]["message"]["content"]

    def chat(self, user_text):
        return self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_text}],
            0.1, 1024)

    def summarize(self, user_text, schedule_result):
        try:
            compact = json.dumps(schedule_result, ensure_ascii=False)[:3000]
            return self._chat(
                [{"role": "system", "content":
                  "你是「老仓管」的汇报员。根据执行结果，用 1-3 句中文向仓库管理员汇报：做了什么、结果如何、有无异常。不要编造结果里没有的数字。"},
                 {"role": "user", "content": "需求：%s\n执行结果：%s" % (user_text, compact)}],
                0.2, 512).strip()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Mock 后端：无模型时验证全链路（明确标注，不冒充真模型）
# ---------------------------------------------------------------------------
class MockBackend(BrainBackend):
    name = "mock"

    def chat(self, user_text):
        dsl = self._translate(user_text)
        return json.dumps(dsl, ensure_ascii=False)

    def _translate(self, text):
        t = text.lower()
        intent = text.strip()[:40]

        def steps(*s):
            return {"dsl_version": "1.0", "intent": intent,
                    "steps": [dict(x, on_error="abort") for x in s]}

        # 1) 台账统计
        if re.search(r"(台账|库存|资产).{0,6}(统计|多少|总数|概况)", t):
            return steps({"op": "ledger.query", "params": {"action": "stats"}})
        # 2) 台账查找
        m = re.search(r"(台账|库存|资产).{0,6}(查|找|搜|看).{0,4}(?P<kw>\S+)", text)
        if m:
            return steps({"op": "ledger.query",
                          "params": {"action": "find", "keyword": m.group("kw")}})
        # 3) 找文件（.xlsx/.c/.docx 等）
        m = re.search(r"(?P<ext>\*\.\w+|\.\w{2,5})", text)
        if re.search(r"(文件|文档|表格|找|查找|搜索)", t) and m:
            root = "E:/"
            rm = re.search(r"在(.{1,40}?)(里|下|目录)", text)
            if rm:
                root = rm.group(1).strip().rstrip("/\\")
            return steps({"op": "file.find",
                          "params": {"pattern": m.group("ext"), "root": root, "depth": 4, "limit": 30}})
        # 4) 台账登记/新增
        if re.search(r"(登记|入库|新增|添加|入账)", t):
            nm = re.search(r"(?:登记|入库|新增|添加|入账)(?:资产)?[：: ]?(?P<n>\S+)", text)
            return steps({"op": "ledger.update",
                          "params": {"action": "insert", "name": nm.group("n") if nm else "未命名资产",
                                     "type": "未分类", "location": "待定", "quantity": 1, "unit": "件"}})
        # 5) 台账删除/出库
        if re.search(r"(删除|出库|移除)", t):
            nm = re.search(r"(?:删除|出库|移除)(?:资产)?[：: ]?(?P<n>\S+)", text)
            return steps({"op": "ledger.query",
                          "params": {"action": "find", "keyword": nm.group("n") if nm else ""}})
        # 6) 系统信息
        if re.search(r"(系统|环境|显卡|gpu|磁盘|内存|配置)", t):
            return steps({"op": "system.info"})
        # 7) 兜底：无法理解
        return {"dsl_version": "1.0", "intent": "无法理解的需求", "steps": []}


# ===========================================================================
# 工厂：自动探测后端
# ===========================================================================
def _ollama_available():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", [])]
        return True, models
    except Exception:
        return False, []


def create_brain(force=None):
    mode = force or os.environ.get("LC_BRAIN", "").strip().lower()
    if mode == "ollama":
        return OllamaBackend()
    if mode == "llama":
        return LlamaBackend()
    if mode == "pyllama":
        return PyLlamaBackend()
    if mode == "mock":
        return MockBackend()
    # 自动探测
    ok, models = _ollama_available()
    if ok:
        model = OLLAMA_MODEL
        if models and not any(m.startswith(model.split(":")[0]) for m in models):
            model = models[0]
        return OllamaBackend(model=model)
    return MockBackend()
