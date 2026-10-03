# OpenAPI 契约演进工作台

在服务端升级场景下，对两份 **OpenAPI 3.x** 文档（基线 / 候选）做契约兼容性比对：

- **请求模式不可收窄**：升级后的服务端必须继续接受旧客户端能发送的一切；
- **响应模式不可放宽**：升级后的服务端不得返回旧客户端无法理解的新形态。

对每个端点、每个媒体类型给出 `兼容 / 破坏性 / 无法判定` 判定，破坏性结果定位到
**JSON Pointer**、解释命中的规则，并生成一份**只在单侧校验通过的最小 JSON 反例**，
可直接在页面上**重放**（对两侧模式重新校验）。端点被删除（`ENDPOINT_REMOVED`）
时，反例是一份**最小 JSON 请求**（方法、已代入具体值的路径、必填 query/header/
cookie 参数、适用时的最小请求体）：重放按两份文档核对**端点存在性**与**基线请求
约束**，明确展示“基线可接受、候选无此端点”。参数可选变必填 / 新增必填、参数或
requestBody 被删除（`PARAMETER_MADE_REQUIRED` / `PARAMETER_ADDED_REQUIRED` /
`PARAMETER_REMOVED`）、响应状态码删除（`STATUS_REMOVED`）、媒体类型删除
（`MEDIA_REMOVED`）等**结构性 finding** 没有可双侧校验的根 schema，其反例是一份
**可编辑的结构化描述符**，重放按 finding 语义分别核对基线是否声明/接受该参数、
请求体或响应、候选是否缺失或收紧，而不再因缺少根 schema 指针而失败。

## 能力一览

- 解析内部与**本地跨文件** `$ref`（ZIP 文档包内任意相对路径）；外部
  `http(s)://` 引用不会被抓取，按“无法解析”报告；
- 展开并合并 `allOf`，识别 `oneOf` / `anyOf` 分支（按结构指纹配对）；
- 比较 `required`、`nullable`（OAS 3.0）/ `type: null`（3.1）、枚举、
  `type`、数值与长度边界、`multipleOf`、数组 `items`、
  `additionalProperties` 等；
- **循环引用安全终止**（自引用、相互引用均不会死循环，统计中可见终止次数）；
- `pattern`、`not`、`if/then/else`、`contains`、`prefixItems` 等
  **未支持的关键字一律标记为“无法判定”**，绝不静默忽略；
- 结果可按严重度（破坏性 / 无法判定 / 兼容）筛选，按端点分组或扁平浏览；
- **调用覆盖视图**：上传 NDJSON 调用记录，按「响应状态码 × JSON 媒体类型」
  生成覆盖格，展示已覆盖格/总格与缺口，未匹配 / 校验失败 / 无法判定逐行列明
  原因；调用值触达 `pattern` 等未支持关键字时该行记为**无法判定**（列出关键字
  与位置，不计覆盖），结果随分析保存并确定性导出；
- **确定性 JSON 导出**（键排序、无多余空白），响应头携带 SHA-256 指纹；
- PostgreSQL 持久化分析记录与原始文档包（用于反例重放）；数据库不可用时
  自动降级为进程内存储；
- Docker Compose 一键启动（Web + PostgreSQL）。

## 一键启动（Docker Compose）

需要本机已安装 Docker 与 Docker Compose。

```bash
docker compose up --build
```

启动后访问：

- 工作台页面：<http://localhost:8000/>
- API 文档（Swagger UI）：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

`docker compose down` 停止；加 `-v` 同时删除数据库卷。

一键启动后，可在页面「比对结果 → **调用覆盖**」标签中选择**基线**或**候选**侧、
上传 NDJSON 调用记录，查看覆盖矩阵与缺口，或直接用下文的 HTTP API 上传/查询/导出。


### 上传内容

- 单个 `.yaml / .yml / .json` OpenAPI 文档；
- 或一个 `.zip` 文档包：包内可含多份相互引用的 YAML/JSON 文件，
  根文档通过 `openapi` 字段自动识别（歧义时优先 `openapi.*` / `index.*`）。

仓库 `examples/baseline` 与 `examples/candidate` 是一份刻意包含各类变化的示例
（跨文件引用、`allOf`、`oneOf`、循环引用、枚举收窄/放宽、nullable、
`pattern` 未支持关键字等），可将两个目录分别打成 ZIP 后上传。
`examples/calls-baseline.ndjson` 是配套的调用记录示例，供「调用覆盖」功能上传
（也可用 `curl -F file=@...` 直接提交，见下文「调用覆盖」）。

## 本地开发（不使用 Docker）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 可选：启动 PostgreSQL 后通过环境变量指向它；不配置则使用内存存储
export DATABASE_URL="postgresql://openapi:openapi@localhost:5432/openapi_contract"

uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pytest -q
```

## HTTP API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/analyses` | multipart 上传 `baseline`、`candidate`，创建分析 |
| `GET` | `/api/analyses` | 历史分析列表 |
| `GET` | `/api/analyses/{id}` | 获取完整报告 |
| `GET` | `/api/analyses/{id}/export?severity=breaking,undetermined` | 确定性 JSON 导出（可按严重度筛选；含已保存的调用覆盖） |
| `POST` | `/api/analyses/{id}/findings/{fid}/replay` | 对反例（可在 body 中用 `{"instance": ...}` 覆盖）做双侧校验重放 |
| `POST` | `/api/analyses/{id}/coverage/{side}` | 上传 NDJSON 调用记录，计算 `baseline`/`candidate` 侧调用覆盖 |
| `GET` | `/api/analyses/{id}/coverage` | 查询两侧调用覆盖的可用性与汇总 |
| `GET` | `/api/analyses/{id}/coverage/{side}` | 获取某侧完整调用覆盖文档（矩阵、缺口、逐行原因） |
| `GET` | `/api/analyses/{id}/coverage/{side}/export` | 确定性导出某侧调用覆盖 JSON（带 SHA-256 指纹头） |
| `DELETE` | `/api/analyses/{id}/coverage/{side}` | 删除某侧调用覆盖结果 |
| `DELETE` | `/api/analyses/{id}` | 删除分析 |
| `GET` | `/api/health` | 服务与存储状态 |

导出响应头：

- `Content-Disposition: attachment; filename="contract-report-<id>-<severity>.json"`
- `X-Report-Fingerprint: <sha256>`（与导出字节一一对应，可用于校验确定性）

## 调用覆盖（Call Coverage）

在已有分析上选择**基线或候选**一侧文档，上传一份 **NDJSON**（每行一个 JSON 对象）
调用记录，引擎把每个调用归并到该侧 operation 的某个**响应覆盖格**，统计
「已覆盖格 / 总格」与缺口，并对未匹配、校验失败、无法判定**逐行给出原因**。
结果随分析一起持久化，并可确定性导出。

### NDJSON 行格式

每行一个 JSON 对象：

| 字段 | 说明 |
| --- | --- |
| `method` | HTTP 方法（大小写不敏感） |
| `path` | 具体请求路径，以 `/` 开头（不含 query） |
| `query` / `headers` / `cookies` | 参数键值对象，可省略（默认 `{}`） |
| `request` | 请求描述：`media_type` + `body`；**缺省 `body` 键表示无请求实体** |
| `response` | 响应描述：整数（或数字字符串）`status`、可选 `media_type` 与 JSON `body`；**缺省 `body` 键表示无响应实体** |

请求/响应头的参数名按 HTTP 惯例**大小写不敏感**匹配，query/cookie 名大小写敏感；
query/header/cookie/path 的文本值会按参数 schema 尝试标量 coerce（如 `"10"`→整数）。

仓库自带示例 `examples/calls-baseline.ndjson`（覆盖、失败、未匹配、非 JSON 排除各若干行）。

### 归属与覆盖规则

1. **路径匹配**：先匹配精确路径；精确路径存在但不支持该方法 → 未匹配；
   否则在声明了该方法的路径模板中做结构匹配（字面段全等、`{var}` 段非空），
   **唯一命中**才归属；同时命中多个模板 → **未归属（ambiguous）**。
2. **覆盖格**：每个 operation 的格为「响应状态码 × JSON 媒体类型」；无 `content`
   的响应单列一个**无实体格**。状态码依次匹配 **精确值 → `1XX`…`5XX` 范围键 →
   `default`**。非 JSON 媒体类型（如 `application/xml`、`text/csv`）不生成格，
   调用携带这些媒体时记「非 JSON 媒体排除」并在 operation 上列明。
   JSON 媒体指 `application/json` 及 `*/*+json`（如 `application/vnd.api+json`）。
3. **判定**：仅当**请求参数**（必填存在性 + schema）、**请求体**（required、
   content 媒体类型、body schema）、**响应体**（status 归属、content 与实体一致性、
   body schema）的**可判定约束全部通过**时才覆盖该格；任一 schema 无法解析/含循环
   → **无法判定**（不给覆盖）；任一约束失败 → **校验失败**。
   已知约束全部通过后，引擎还会**沿调用实际取值遍历解析后的 schema 图**
   （本地 `$ref`、嵌套属性、数组元素、`oneOf`/`anyOf` 组合分支）：只要该行
   能否通过仍依赖 `pattern`、`not`、`contains` 等**未支持关键字**，该行记为
   **无法判定**并在原因中列出关键字及其位置（跨文件时含文件名），不增加
   已覆盖格；**未触达的可选属性不会阻断覆盖**，与取值类型无关的关键字
   （如整数值上的 `pattern`）也不会误报。
4. **零分母**：一个 operation 只有非 JSON 媒体（或无任何可覆盖格）时标记
   **「无适用格」**，不参与覆盖率分母。

### 实际请求与响应示例

针对示例文档包创建分析后，对**基线**侧上传调用记录：

```bash
curl -s -X POST http://localhost:8000/api/analyses \
  -F baseline=@examples/baseline.zip \
  -F candidate=@examples/candidate.zip -o /tmp/report.json

AID=$(python3 -c "import json;print(json.load(open('/tmp/report.json'))['analysis_id'])")

curl -s -X POST "http://localhost:8000/api/analyses/$AID/coverage/baseline" \
  -F file=@examples/calls-baseline.ndjson
```

上传体是 multipart（字段名 `file`）。响应（节选）形如：

```json
{
  "schema_version": "1.0",
  "analysis_side": "baseline",
  "source_file": "calls-baseline.ndjson",
  "summary": {
    "operations_total": 5, "operations_applicable": 5,
    "operations_without_cells": 0,
    "cells_total": 7, "cells_covered": 6, "cells_gap": 1,
    "calls_total": 10, "calls_covered": 6,
    "calls_validation_failed": 2, "calls_undetermined": 0,
    "calls_unmatched": 1, "calls_ambiguous": 0,
    "calls_excluded_media": 1, "calls_invalid": 0
  },
  "operations": [
    {
      "path": "/pets/{petId}", "method": "GET", "applicable": true,
      "covered": 2, "total": 2,
      "cells": [
        {"key": "200|application/json", "status": "200", "kind": "content",
         "media_type": "application/json", "covered": true,
         "covered_by_line": 3},
        {"key": "404|(no content)", "status": "404", "kind": "no_content",
         "media_type": null, "covered": true, "covered_by_line": 4}
      ],
      "gaps": [], "excluded_media": []
    }
  ],
  "rows": [
    {"line": 1, "outcome": "covered", "method": "GET", "path": "/pets",
     "status": "200", "operation": "GET /pets",
     "matched_template": "/pets", "cell": "200|application/json",
     "resolved_status": "200", "reasons": []},
    {"line": 7, "outcome": "validation_failed", "method": "GET",
     "path": "/pets", "status": "200", "operation": "GET /pets",
     "matched_template": "/pets", "cell": null,
     "reasons": ["查询参数 'status'不满足契约约束: #: 值不在枚举 [...] 中"]},
    {"line": 8, "outcome": "unmatched", "method": "GET",
     "path": "/unknown/path", "status": "200", "operation": null,
     "matched_template": null, "cell": null,
     "reasons": ["没有路径模板匹配 GET /unknown/path"]}
  ]
}
```

`outcome` 取值：`covered` / `validation_failed` / `undetermined` /
`unmatched` / `ambiguous`（多模板未归属）/ `excluded_media` / `invalid`
（行本身不是合法 NDJSON 调用对象）。整份文件没有任何可解析行时返回 `400`。

查询与导出：

```bash
# 两侧可用性 + 汇总
curl -s "http://localhost:8000/api/analyses/$AID/coverage"

# 某侧完整文档
curl -s "http://localhost:8000/api/analyses/$AID/coverage/baseline"

# 确定性导出（键排序、无多余空白），指纹在响应头
curl -s -D - "http://localhost:8000/api/analyses/$AID/coverage/baseline/export" \
  -o coverage-baseline.json
# X-Coverage-Fingerprint: <sha256>

# 已保存的覆盖同时内嵌进分析总导出 call_coverage 字段
curl -s "http://localhost:8000/api/analyses/$AID/export" -o report.json
```

重复上传同一侧会**替换**旧结果；`DELETE` 删除指定侧；分析被删除时其覆盖一并删除。
相同 NDJSON 输入永远产生字节一致的导出（含逐行原因，覆盖格只记录最早一行覆盖调用）。

## 判定规则（方向：基线 → 候选）

### 请求侧（不可收窄）

新增必填参数/属性、可选变必填、枚举收窄、类型收窄、数值/长度下界抬高或上界压低、
新增 `multipleOf`、`additionalProperties` 变为 `false`、删除参数/媒体类型/端点等
→ **破坏性**。

### 响应侧（不可放宽）

枚举新增取值、类型放宽、允许 `null`、`oneOf` 新增分支、删除必有字段、
数值/长度边界放宽、删除 `additionalProperties:false` 等 → **破坏性**。

### 无法判定

存在未支持的关键字（`pattern`、`not`、`if/then/else`、`contains` …）、
`$ref` 无法解析（含外部引用）、`format` 语义变化、多重非空类型数组、
`oneOf` 分支无法可靠配对、模式自相矛盾等 → **无法判定**，并给出对应 JSON Pointer。

每条破坏性结果包含：

- `rule` / `rule_title` / `rule_detail`：命中规则与解释；
- `pointer.baseline` / `pointer.candidate`：定位（跨文件时另附 `file`）；
- `witness.instance`：最小 JSON 反例；
- `witness.self_check`：生成时对双侧模式的自检结果；
- 重放接口再次独立校验，并给出失败原因列表。

### 端点删除（ENDPOINT_REMOVED）的反例与重放

候选整体移除某个 operation 时，候选侧**不存在可校验的 schema**，因此该结果不
走模式重放，而是生成并核对一份**端点级最小请求**：

- `witness.instance` 形如
  `{"method": "POST", "path": "/shops/5/orders/abc", "query": {...},
  "header": {...}, "cookie": {...}, "body": {...}}`：路径模板占位符已被满足
  基线约束的具体值替换；只包含**必填**参数（可选参数按旧客户端惯例省略）；
  存在**必填**请求体时附最小 JSON `body`，可选请求体则省略并在 `caveats` 说明。
- 重放分别检查两份文档中端点是否存在（候选保留了 path item 但删掉该 method 时
  会明确区分），并把请求重新对照基线 operation 的路径/查询/请求头/Cookie 参数
  与请求体 schema 校验；结论展示“**基线可接受、候选无此端点**”。
- 当基线请求无法**可靠构造**（必填参数的 `$ref` 无法解析、只有
  `application/octet-stream` 等无法综合的请求体媒体类型、最小实例自校验失败
  等），破坏性结论**保留**，但 `witness.verifiable=false` 并给出
  `unverifiable_reason`：页面标记“反例不可验证”，重放不会伪称单侧通过；
  仍可在页面编辑器中手写请求后再次重放。
- 表单类媒体类型（`application/x-www-form-urlencoded` /
  `multipart/form-data`）按结构化 schema 校验 body，并提示未重放实际线缆编码。

反例均可在结果抽屉的 JSON 编辑器中修改后重放；模式级（参数 / 请求体 / 响应中的
schema 变化）反例走双侧 schema 校验，端点级反例走上文的存在性 + 基线约束核对。

### 结构性 finding 的反例与重放

以下 finding 在候选侧（或基线侧）根本不存在可校验的媒体 schema，旧版本会错误地
落入模式重放并报“重放信息缺少根 schema 指针”；现在它们生成**结构化描述符**
反例，重放从存储的两份文档**重新推导**声明与约束：

| 规则 | 反例描述符（可编辑） | 重放核对 |
| --- | --- | --- |
| `PARAMETER_MADE_REQUIRED`（参数） | `{method, path, parameter, in, value}` | 基线声明该参数且**可选**；候选把它收紧为**必填** |
| `PARAMETER_ADDED_REQUIRED`（参数） | 同上 | 基线**未声明**该参数；候选声明且**必填** |
| `PARAMETER_REMOVED`（参数） | 同上 | 基线声明并按参数 schema **接受** `value`；候选**无此参数** |
| `PARAMETER_*`（requestBody） | `{method, path, media_type, body}` | 基线 requestBody 的声明/必填与 `body` 的 schema 校验；候选删除 requestBody 或将其收紧为必填 |
| `STATUS_REMOVED` | `{method, path, status_code, media_type, body}` | 基线声明该状态码（有 schema 时校验 `body`）；候选**无此状态码** |
| `MEDIA_REMOVED` | `{method, path, location, status_code?, media_type, body}` | 基线声明并接受/返回该媒体类型且 `body` 通过其 schema；候选**无此媒体类型** |

抽屉里分别展示“基线核对 / 候选核对”两张卡片（声明情况、`required`、媒体类型、
schema 错误）以及携带值/载荷的独立校验；只有当两侧事实都与 finding 语义一致时
才给出“**反例可验证**”，否则保留破坏性结论并标记“反例不可验证”，可在编辑器中
修改描述符后再次重放。跨文件 `$ref` 的 requestBody / 响应会先解析到实际定义位置
再取 schema。

## 目录结构

```
app/
  api/routes.py          # FastAPI 路由（上传、分析、导出、重放、历史）
  services/
    loader.py            # 单文件 / ZIP 包加载与根文档识别
    pointer.py           # JSON Pointer (RFC 6901) 工具
    resolver.py          # 本地/跨文件 $ref 解析（外部引用拒绝抓取）
    schemagraph.py       # 规范化 Schema 图：$ref、allOf 合并、循环终止
    instance.py          # 最小实例生成 + 模式校验（反例重放基础）+ 沿实际取值检测未支持关键字
    endpointrequest.py   # 端点级最小 JSON 请求构造与基线请求约束核对
    structuralreplay.py  # 结构性 finding（参数/请求体/状态码/媒体类型增删）的结构化反例与语义重放
    comparator.py        # 兼容性比较引擎与最小反例综合
    coverage.py          # 调用覆盖：NDJSON 解析、路径归属、参数/请求体/响应体约束核对、未支持关键字沿值判定、覆盖格
    replay.py            # 双侧重放校验（模式级 + 端点级）
    rules.py             # 规则目录与严重度
  analysis.py            # 编排：加载→比较→序列化→存储
  serialization.py       # 确定性 JSON 导出
  db.py / store.py       # PostgreSQL 持久化 + 内存降级（含调用覆盖）
  static/                # 原生 JS 前端（无框架、无构建步骤）
examples/                # 基线 / 候选示例文档包 + calls-baseline.ndjson 调用记录
tests/                   # pytest（引擎/循环 27 项 + API 25 项 + 调用覆盖 26 项）
```

## 说明与边界

- 比对基于 OpenAPI 3.x 的 Schema 约束语义；`format` 默认不做强制校验，
  因此 format 变化只判“无法判定”而非破坏；
- 反例综合在复杂组合约束下是**尽力而为**：报告的 `self_check` 会如实标注
  是否严格“仅单侧通过”；不满足时可在页面编辑器里手动修改实例后再次重放；
- 不抓取任何网络资源，所有分析均在本地/容器内完成。
