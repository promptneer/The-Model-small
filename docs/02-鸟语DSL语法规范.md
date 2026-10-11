# 鸟语 DSL 语法规范（v1.0）

> 鸟语 DSL（Bird-DSL）是「老仓管」的
>
> **中间语言**
>
> ：模型输出它、调度器执行它。
> 设计目标：
>
> **机器可校验、原子可回滚、非法可拦截**
>
> 。
> 载体：JSON 对象（小模型生成 JSON 的稳定性最高）。



***

## 1. 顶层结构



```
{
  "dsl_version": "1.0",
  "intent": "一句话描述用户意图（给人看、给日志看）",
  "steps": [ "≥1 个原子操作步骤（≤10 步）" ]
}
```



| 字段           | 类型     | 必填        | 说明             |
| ------------ | ------ | --------- | -------------- |
| dsl\_version | string | 否（默认 1.0） | 协议版本           |
| intent       | string | 是         | 用户需求的摘要，模型必须写  |
| steps        | array  | 是         | 有序执行步骤，1\~10 个 |



***

## 2. 步骤（step）结构



```
{
  "op": "file.find",
  "params": { "...": "..." },
  "guard": { "require_dir": "E:/warehouse" },
  "on_error": "abort",
  "undo": true
}
```



| 字段        | 类型     | 必填 | 说明                      |
| --------- | ------ | -- | ----------------------- |
| op        | string | ✅  | 原子操作名，必须在白名单（见 §3）      |
| params    | object | ✅  | 操作参数，按各 op 的 schema 校验  |
| guard     | object | 否  | 前置校验，不满足则整单中止           |
| on\_error | string | 否  | `abort`（默认）或 `continue` |
| undo      | bool   | 否  | 失败时是否回滚（写操作默认 true）     |

### 2.1 guard 前置校验类型



| 写法                       | 含义            |
| ------------------------ | ------------- |
| `{"require_dir": "路径"}`  | 路径必须存在且为目录    |
| `{"require_file": "路径"}` | 路径必须存在且为文件    |
| `{"require_key": "参数名"}` | params 中该参数非空 |



***

## 3. 原子操作目录（op 白名单）

### 3.1 `file.find` —— 文件查找

遍历、匹配、汇总全部由脚本完成，模型只给条件。



| 参数      | 类型      | 必填 | 说明                           |
| ------- | ------- | -- | ---------------------------- |
| pattern | string  | ✅  | 通配模式，如 `*.xlsx`、`*.c`、`*报表*` |
| root    | string  | 否  | 起始目录（默认用户主目录）                |
| keyword | string  | 否  | 文件名必须包含的关键字                  |
| depth   | integer | 否  | 最大递归深度 0\~20（默认 5）           |
| limit   | integer | 否  | 最多返回条数 1\~200（默认 50）         |

返回：`{op, root, pattern, matched, total, truncated, files:[{path,name,size,mtime}]}`

示例：



```
{"op":"file.find","params":{"root":"E:/warehouse","pattern":"*.xlsx","depth":3,"limit":30}}
```

### 3.2 `ledger.query` —— 台账查询



| 参数      | 类型      | 必填 | 说明                                                     |
| ------- | ------- | -- | ------------------------------------------------------ |
| action  | string  | ✅  | `find` / `list_type` / `list_all` / `stats` / `recent` |
| keyword | string  | 否  | find 时必填：名称关键词                                         |
| type    | string  | 否  | list\_type 时必填：资产类型                                    |
| limit   | integer | 否  | recent 返回条数 1\~100（默认 10）                              |

示例：



```
{"op":"ledger.query","params":{"action":"stats"}}
{"op":"ledger.query","params":{"action":"find","keyword":"手套"}}
{"op":"ledger.query","params":{"action":"recent","limit":5}}
```

### 3.3 `ledger.update` —— 台账写入（可回滚）



| 参数                                    | 类型      | 必填               | 说明                                                    |
| ------------------------------------- | ------- | ---------------- | ----------------------------------------------------- |
| action                                | string  | ✅                | `insert` / `update` / `delete`                        |
| name                                  | string  | insert 必填        | 资产名称（唯一）                                              |
| type / location / unit / owner / note | string  | 否                | 资产属性                                                  |
| quantity                              | number  | 否                | 数量（默认 1）                                              |
| id                                    | integer | update/delete 必填 | 资产 id                                                 |
| fields                                | object  | update 必填        | 要更新的字段，键限：name/type/location/quantity/unit/owner/note |

**回滚语义**：每次写入返回 `undo`，调度器在后续步骤失败时逆序执行：



* insert → `undo={action:delete, id}`（删除新记录）

* update → `undo={action:restore, id, old:{旧值}}`（还原旧值）

* delete → `undo={action:reinsert, row:{完整旧行}}`（插回原记录）

示例：



```
{"op":"ledger.update","params":{"action":"insert","name":"新叉车","type":"工具","location":"维修区","quantity":1,"unit":"辆"}}
{"op":"ledger.update","params":{"action":"update","id":3,"fields":{"quantity":2,"note":"补充入库"}}}
{"op":"ledger.update","params":{"action":"delete","id":9}}
```

### 3.4 `system.info` —— 本机环境

无参数。返回 GPU / 显存 / 内存 / 磁盘 / Python 版本。



***

## 4. 完整示例

### 4.1 多步骤：先查台账确认，再补货登记



```
{
  "dsl_version": "1.0",
  "intent": "查一下防静电手套库存，如果少就补货到 50 双",
  "steps": [
    {"op": "ledger.query", "params": {"action": "find", "keyword": "防静电手套"}},
    {"op": "ledger.update", "params": {"action": "update", "id": 8, "fields": {"quantity": 50}}}
  ]
}
```

### 4.2 带前置校验与容错



```
{
  "dsl_version": "1.0",
  "intent": "在仓库目录找 C 源码",
  "steps": [
    {
      "op": "file.find",
      "params": {"root": "E:/warehouse", "pattern": "*.c", "depth": 4},
      "guard": {"require_dir": "E:/warehouse"},
      "on_error": "continue"
    },
    {"op": "ledger.query", "params": {"action": "stats"}}
  ]
}
```



***

## 5. 模型输出约束（大脑侧的硬规则）

system prompt 明确要求，模型**只输出一个 JSON 对象**：



* 禁止 markdown 代码块包裹（解析器可容错剥除，但不鼓励）；

* 禁止散文、解释、寒暄；

* 无法理解需求时输出 `{"dsl_version":"1.0","intent":"无法理解的需求","steps":[]}`。



***

## 6. 非法输出与拦截行为（调度器视角）



| 模型输出                                         | 判定          | 用户看到                           |
| -------------------------------------------- | ----------- | ------------------------------ |
| `{"steps":[{"op":"shell.run",...}]}`         | 未注册 op      | 「模型输出的不是合法鸟语 DSL（已拦截，未执行任何操作）」 |
| 「好的，我来帮你查找文件……」                              | 非 JSON      | 同上                             |
| `{"steps":[{"op":"file.find","params":{}}]}` | 缺必填 pattern | 「执行失败：参数校验失败：缺少必填参数: pattern」  |
| `{"steps":[]}`                               | 空步骤         | 「无法理解的需求」                      |

> 拦截原则：
>
> **宁可拒绝，绝不猜测执行。**
>
>  所有拦截发生在调度器层，不触碰台账与文件系统。