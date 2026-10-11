# 老仓管 · 轻量本地仓库智能体

> 会听人话的本地仓库管家：**小模型只做大脑，脚本负责体力**。
> 你说人话 → 它翻译成「鸟语DSL」指令 → 调度器执行脚本 → 结果讲给你听。
> 无需联网、显存占用小、普通电脑就能跑（RTX5060 8G / GTX1650 4G 均可）。

---

## 核心思路

把一件事拆成两半，各干各的，谁也别越界：

| 角色 | 负责 | 不负责 |
|---|---|---|
| **大脑（小模型 ≤3B）** | 听懂模糊人话、判断目标、输出「鸟语DSL」、异常判断 | 遍历文件、读写台账、循环检索 |
| **手脚（原子脚本）** | 文件查找、台账读写、检索统计 | 语义理解 |
| **翻译官（调度器）** | 解析 DSL、白名单校验、guard/schema 前置校验、失败回滚、审计记录 | — |

好处：模型只管动脑 → 显存占用小、速度快；脚本只管干活 → 结果确定、绝不瞎编。

## 三大机制

1. **拦截**：模型输出非法指令（未注册操作、非 JSON、参数不合法）→ 调度器直接拦下，零执行。宁可拒绝，不猜着办。
2. **原子性**：写操作要么完整成功，要么失败时逆序回滚，台账回到执行前状态。
3. **审计**：每次操作写入审计日志，`/recent` 随时可查。

## 鸟语DSL 是什么

模型输出的中间语言（JSON），最小单元是**原子操作**：不可拆分、可回滚、带参数校验。

```json
{"dsl_version":"1.0","intent":"查防静电手套库存",
 "steps":[{"op":"ledger.query","params":{"action":"find","keyword":"防静电手套"}}]}
```

当前内置 4 个原子操作：

| 操作 | 作用 |
|---|---|
| `file.find` | 文件查找（按模式/关键字/目录/深度） |
| `ledger.query` | 台账查询（find / stats / recent / list_all / list_type） |
| `ledger.update` | 台账写入（insert / update / delete，全部可回滚） |
| `system.info` | 本机环境（GPU / 内存 / 磁盘 / Python） |

完整规范见 `docs/02-鸟语DSL语法规范.md`。

## 目录结构

```
laocang-guan/
├── main.py               # 交互入口（CLI）
├── test_run.py           # 端到端自测（15 项，不依赖模型）
├── seed_ledger.py        # 台账初始化（示例资产 + 业务规则）
├── requirements.txt      # 依赖说明
├── core/
│   ├── brain.py          # 大脑：Ollama / llama.cpp / pyllama(GGUF) / mock 可插拔
│   ├── dsl_parser.py     # 翻译官·审稿：DSL 解析、结构校验、非法拦截
│   ├── scheduler.py      # 翻译官·调度：执行 / 回滚 / 容错
│   ├── registry.py       # 原子操作注册表 + 参数 schema 校验
│   └── ledger.py         # 台账（SQLite：白名单查询 + 审计 + undo）
├── atoms/                # 手脚：4 个示例原子操作
│   ├── file_find.py
│   ├── ledger_query.py
│   ├── ledger_update.py
│   └── system_info.py
├── docs/                 # 三份技术文档
│   ├── 01-架构说明.md
│   ├── 02-鸟语DSL语法规范.md
│   └── 03-部署与测试教程.md
└── data/                 # 台账数据库（运行时自动生成）
```

## 快速开始

按你的机器选一条路线（详细步骤见 `docs/03-部署与测试教程.md`）：

### 路线 A：Ollama（推荐，RTX5060 8G）

```bash
ollama pull qwen2.5:3b
python main.py            # 自动探测到 Ollama
```

### 路线 B：llama-cpp-python 直读 GGUF（GTX1650 4G 已实测）

```bash
pip install llama-cpp-python==0.3.19   # Windows 需本机编译，见 docs/03
# 下载 Qwen2.5-1.5B GGUF（约 1.1GB，hf-mirror 可下）
export LC_BRAIN=pyllama
export LC_GGUF=/path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf
python main.py
```

### 路线 C：mock（无模型验证链路）

```bash
python main.py --brain mock
```

## 使用示例

```
仓管需求> 查台账里有没有防静电手套
仓管需求> 看一下仓库现在有多少资产
仓管需求> 在 E:/ 找所有 .xlsx 文件
仓管需求> 登记一台新叉车，放维修区，1 辆
仓管需求> 系统环境怎么样
```

斜杠命令：`/ledger` 看全部资产 · `/stats` 统计 · `/recent 5` 最近记录 · `/mode` 看后端 · `/quit` 退出

## 测试

```bash
python test_run.py        # 15 项自测：调度/回滚/拦截/容错，不依赖模型
```

实测：GTX1650 4G 上 Qwen2.5-1.5B 加载 2.3s，单次需求 3~8s，内存约 2GB。

## 文档

- `docs/01-架构说明.md` —— 系统设计、分层、容错、显存选型
- `docs/02-鸟语DSL语法规范.md` —— DSL 完整语法、原子操作参数表、示例
- `docs/03-部署与测试教程.md` —— 三条路线的部署、启动、测试、FAQ

## 扩展一个新原子操作（三步）

1. `atoms/` 新建 `xxx.py`，继承 `Atom`，实现 `run()`（写操作再实现 `rollback()`），用 `@register` 注册；
2. `atoms/__init__.py` 加一行 import；
3. `core/dsl_parser.py` 的 `VALID_OPS` 登记操作名，并把用法写进 `brain.py` 的 system prompt。

---

**License**：MIT（本项目为学习/内部工具项目，代码可自由使用）。
