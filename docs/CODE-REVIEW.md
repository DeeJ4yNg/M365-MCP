# 代码 Review 指南

本指南针对本项目的真实边界设计，不是通用 Python checklist。

审查目标不是“代码能否跑通”，而是“Agent 是否只能执行经过批准的 Microsoft 365 动作”。
审查顺序应先看信任边界，再看执行链，再看业务细节，最后看测试和文档。
任何让人可以绕过 Catalog、Review、confirmation 或权限校验的改动都应阻断合并。

## 1. 项目入口

项目根目录：

```text
/Users/deej4y/WorkBuddy/2026-10-31-20-26-30
```

代码入口按职责划分：

| 路径 | 职责 | Review 优先级 |
|---|---|---|
| `src/m365_mcp/models.py` | Catalog 记录、Graph binding、PowerShell binding 的模型校验 | 最高 |
| `src/m365_mcp/catalog.py` | 加载 Catalog、Review registry、enabledTools，词法评分与执行 gate | 最高 |
| `src/m365_mcp/search_lexicon.py` | 英文 normalization、产品短语、concept tags 与双语 synonyms | 高 |
| `src/m365_mcp/search_service.py` | 远程 embedding、semantic index、hybrid 打分和 fallback | 最高 |
| `src/m365_mcp/auth.py` | 入口 Entra JWT 的 role、client、tenant 授权 | 最高 |
| `src/m365_mcp/confirmation.py` | 写操作 confirmation 验证、参数绑定和防重放 | 最高 |
| `src/m365_mcp/server_common.py` | 五个 MCP Tool、错误映射、health、响应安全头 | 最高 |
| `src/m365_mcp/graph_client.py` | 证书认证、Graph HTTP、重试、分页、结果大小 | 高 |
| `src/m365_mcp/graph/executor.py` | Graph path、query、body 的固定映射 | 高 |
| `src/m365_mcp/powershell/executor.py` | 参数映射、一次性 `pwsh` 进程和超时 | 高 |
| `src/m365_mcp/powershell/worker.ps1` | 固定 cmdlet 执行、连接和 JSON envelope | 高 |
| `src/m365_mcp/graph/server.py` | Graph Server 组装和启动 | 中 |
| `src/m365_mcp/powershell/server.py` | PowerShell Server 组装和启动 | 中 |
| `src/m365_mcp/config.py` | 环境变量、secret 文件、启动校验 | 高 |
| `src/m365_mcp/audit.py` | 审计字段和敏感信息边界 | 中 |
| `config/enabledTools.json` | 业务动作 allow-list | 最高 |
| `config/review-registry.json` | 人工 Review 决策和 evidence | 最高 |
| `deploy/` | 镜像、用户、module pin、只读和 secret | 高 |
| `docker-compose.yml` | 网络隔离、权限、secret、挂载 | 高 |
| `tests/` | 安全负向测试和退行保护 | 高 |

## 2. 工具到底定义在哪里

先纠正一个常见误解：本项目没有为 298 个业务动作各写一个 Python function。

MCP Client 直接看到的是五个治理工具：

1. `search_actions`
2. `describe_action`
3. `execute_read`
4. `execute_write`
5. `get_tool_inventory`

这五个工具定义在：

```text
src/m365_mcp/server_common.py
```

业务动作是 Catalog 数据，不是独立 MCP tool。
Agent 先通过 `search_actions` 找到 action ID 和输入 Schema，再通过 `execute_read` 或 `execute_write` 执行。
这样做避免把 298 份 Schema 全部注入每一轮 Agent context。
同时让权限、Review、风险和执行入口保持统一。

### 2.1 Graph 动作定义

Graph Catalog 的唯一源文件：

```text
scripts/generate_graph_catalog.py
```

生成后的运行文件：

```text
src/m365_mcp/catalogs/graph.json
```

当前 181 个 Graph action。
每个 action 包含业务名称、domain、permission、read/write、risk、输入 Schema 和固定 Graph binding。
Graph binding 只允许 `GET`、`POST`、`PATCH`、`PUT`、`DELETE` 和已声明的 v1.0 path。
Graph action 的 endpoint、query 映射、body 映射、常量 body 都在生成脚本的 `read()` 或 `write()` 定义里。

不要直接手改 `graph.json`。
应修改生成脚本，再执行：

```bash
uv run python scripts/generate_graph_catalog.py
```

### 2.2 PowerShell 动作定义

PowerShell Catalog 源文件：

```text
src/m365_mcp/catalogs/powershell.json
```

当前 117 个 action，其中 105 个 implemented、4 个 planned、8 个 unsupported。
每个 action 固定声明：

```text
service
cmdlet
parameters
constant_parameters
input_schema
operation_type
risk_level
```

Python 端没有为每个 cmdlet 写包装函数。
`PowerShellExecutor` 读取 action 的固定 binding，映射参数后启动一次性 `pwsh` worker。
`worker.ps1` 再次从 Catalog 读取同一个 action，核对 cmdlet 后才执行。
这是双层校验，防止 Python 端被替换后直接执行任意命令。

### 2.3 运行时如何决定能不能执行

判断顺序在 `ActionCatalog.is_enabled()` 和 `ActionCatalog.require_action()`：

1. action 存在于 Catalog。
2. `implementation_status=implemented`。
3. Review registry 中状态为 `approved`。
4. action 在 `config/enabledTools.json` 对应 server 数组里。
5. 调用的 MCP tool 与 action 的 read/write 类型一致。
6. 参数通过 JSON Schema。
7. 写操作还要通过 confirmation 验证和 jti 占用。

修改任何一步的代码都要视为安全变更，不是普通重构。

## 3. Review 前先跑的自动检查

有 Git 时先看变更范围：

```bash
git status --short
git diff --stat
git diff
```

基础质量门：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run m365-inventory validate
uv run m365-inventory stats
```

Graph Catalog 变更：

```bash
uv run python scripts/generate_graph_catalog.py
```

生成后必须检查 diff。
如果生成文件变化但生成脚本没变，说明有人手改了生成物，应拒绝。

PowerShell Catalog 变更：

```bash
pwsh -NoProfile -File scripts/Validate-PowerShellCatalog.ps1
```

该检查只验证 worker 语法和可离线导入的 cmdlet 参数。
涉及 Exchange remote cmdlet 时，Review 还必须要求 tenant evidence。

Docker 变更：

```bash
docker compose config
docker compose build graph-mcp powershell-mcp
docker compose up -d valkey graph-mcp powershell-mcp
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8001/readyz
```

当前开发机没有 Docker daemon，镜像构建仍属于目标 Host 验收项。
审查 Dockerfile 时不能因为“CI 过了”就假设镜像可运行。

## 4. 七个必须阻断的 Review 发现

以下任一项出现都必须 blocking，不能以“下个 PR 修”放过。

### 4.1 出现任意请求入口

禁止接受调用方提供的：

```text
URL
HTTP method
Graph path
PowerShell script
cmdlet name
module name
expression
```

如果代码出现 `generic_request`、`run_cmdlet`、`invoke_script`、`eval`、`Invoke-Expression`，直接拒绝。
任何新能力必须落成 Catalog 中的固定 action。

### 4.2 disabled action 可以执行

必须确认 `execute_read` 和 `execute_write` 都调用 `require_action(..., require_enabled=True)`。
不能只检查 action 是否存在。
不能只检查 `implementation_status`。
不能相信 Agent 自己说 action 已经批准。

### 4.3 写操作绕过 confirmation

所有 write 必须：
1. 具有 write role。
2. action Review approved。
3. action enabled。
4. Schema 校验通过。
5. confirmation 签名、audience、action、params digest、actor、exp 全部通过。
6. jti 在上游调用前占用成功。

如果 confirmation 校验被放在 upstream 调用之后，直接拒绝。
如果写操作在上游失败后自动重试，默认拒绝，除非有明确幂等键和业务证据。

### 4.4 入口 token 被透传

MCP 的入口 JWT 只能用于 MCP 授权。
Graph 和 PowerShell 必须使用各自独立的上游证书身份。
检查 `Authorization` header 是否只用于 MCP ingress，不能被拼接到上游请求。
`GraphClient` 和 `worker.ps1` 不应出现入口 Bearer token。

### 4.5 参数或 token 进入普通日志

审计日志只应出现：
`server`、`action`、`operation`、`risk`、`actor`、`correlation_id`、`outcome`、`duration_ms`、错误码和 approver。
不应出现完整 params、邮件正文、文件内容、证书 password、入口 token 或 confirmation token。
`reason` 只允许截断后的业务原因。

### 4.6 Schema 允许额外字段

所有 input schema 必须满足：
```json
{
  "type": "object",
  "additionalProperties": false
}
```
每个 property 都要有 `description`。
路径参数必须 required。
字符串必须有上限。
数组必须有 `maxItems`。
枚举必须显式。
额外字段被静默忽略会让审批参数与执行参数不一致。

### 4.7 English lexicon 引入危险同义关系

`search_lexicon.py` 会直接影响候选排序，Review 时检查：

1. 英文是主语言，中文 alias 只做补充。
2. 产品缩写是否有明确上下文，例如 DL、EXO、SPO、MFA、NDR、ESP。
3. `disable`、`delete`、`remove`、`wipe`、`retire`、`revoke` 没有互相等同。
4. `reset password` 和 `reset passcode` 没有混为同一概念。
5. 症状短语不会把只读诊断类 action 排到危险的 write action 前面。
6. concept action hints 不会把通用词 `user`、`policy`、`report` 加到所有动作。
7. 新增 alias 有对应的 ticket 例句或评测样本。
8. 不引入没有边界的 WordNet 泛同义词。
9. phrase normalization 不会把错误码或 UPN 误改成其他词。
10. concept 权重变化有回归测试覆盖。

### 4.8 Hybrid search 绕过治理

Semantic search 只能改变候选排序，不能改变谁可以执行动作。

必须确认：

1. embedding 输入不包含 M365 token。
2. Agent 不能提供 embedding vector。
3. embedding 服务不能返回 enabled 状态或绕过 Review。
4. domain、operation、enabled 和 review filter 在 semantic 结果上仍生效。
5. fallback 必须显式标记 `search_mode`。
6. query 和 action vector 必须使用同一模型、revision、dimension 和 instruction。
7. 维度异常、零向量、NaN 或 Inf 必须拒绝。
8. `semantic_floor`、`semantic_ceiling` 和权重不能未经 benchmark 就宣称生产可用。
9. embedding endpoint 必须是配置值，不能由 Agent 输入 URL。
10. query 原文默认不能进入集中日志。

### 4.9 action 默认启用

新 action 必须：
```json
{
  "review_status": "pending",
  "enabled": false
}
```
`enabled=true` 只允许通过 `enabledTools.json` 在运行时派生。
如果 PR 修改 Review registry 或 enabledTools 来“顺便让测试通过”，必须单独审查并有 evidence。

## 5. Catalog Review 方法

### 5.1 单个 action 的五分钟 Review

对每个新增或修改 action 按顺序检查：

| 检查项 | 关键问题 |
|---|---|
| Name | 是否唯一、稳定、snake_case、能表达业务动作 |
| Domain | 是否使用统一领域名 |
| Description | 是否说明能力、对象和边界，没有 prompt 指令 |
| Backend | Graph 还是 PowerShell，是否选对 |
| Endpoint/cmdlet | 是否固定，是否 v1.0/受支持模块 |
| Permission | 是否最小 Application permission 或准确 Entra role |
| Operation | read/write 是否与副作用一致 |
| Risk | 是否与删除、授权、wipe、策略变更匹配 |
| Schema | 是否拒绝额外字段，ID 和数组是否有限制 |
| Binding | 每个 Schema 参数是否有明确映射，无隐式参数 |
| Docs | 是否链接官方 Microsoft Learn 或 PnP 文档 |
| Review | 是否仍然 pending |
| Tests | 是否有正向、负向和失败语义测试 |

写操作的描述必须说明不可逆后果。
例如 delete、wipe、role assignment、Conditional Access update 不能只写“修改配置”。

### 5.2 Graph binding Review

重点看 `GraphRequestSpec` 和 `GraphExecutor`：

1. `path` 必须是相对固定 path。
2. path 中的 `{param}` 必须出现在 Schema required。
3. 不允许 `://`、query string、fragment 或双斜杠。
4. query 只能映射到已声明参数。
5. body 只能映射到已声明参数。
6. `body_const` 中的占位符必须 required。
7. `payload` 模式不能绕过 Schema 变成任意 URL。
8. `method=GET` 必须是 read。
9. `DELETE` 必须是 write。
10. 写操作不应有自动重试。

特别注意 `GraphClient.get_paginated()`：

1. `nextLink` 必须同 origin。
2. path 必须仍在 `/v1.0` 下。
3. 不能跟随任意重定向。
4. 不能把 nextLink 原样作为新工具参数暴露给 Agent。
5. 页数和结果大小必须有上限。

### 5.3 PowerShell binding Review

重点看 `PowerShellRequestSpec` 和 `worker.ps1`：

1. cmdlet 是否在允许前缀里。
2. cmdlet 是否固定，不接受参数化名称。
3. 参数名是否真实存在于目标 module。
4. constant 是否不能被调用方覆盖。
5. read/write 分类是否与 cmdlet 副作用一致。
6. worker 是否从 stdin 读 JSON，而不是拼接命令字符串。
7. worker 是否重新校验 action 和 cmdlet。
8. 是否没有 `Invoke-Expression`。
9. 超时是否终止整个进程组。
10. 输出超过上限时是否安全处理。

如果新增 cmdlet 只在连接后出现，单元测试只能验证 Schema。
Review 必须要求隔离 tenant 的 evidence 或 PowerShell metadata 检查记录。

## 6. 入口认证 Review

文件：`src/m365_mcp/auth.py` 和 `server_common.py`。

确认：

1. `MCP_AUTH_MODE=none` 必须同时显式允许 insecure dev。
2. production 不允许无认证启动。
3. `audience` 必须匹配对应 Server。
4. `issuer` 必须来自配置 tenant。
5. `tid` 必须匹配。
6. `roles` 必须包含读或写 app role。
7. client ID 必须在 allow-list。
8. 入口 token 不能被上游复用。
9. 工具本身不能靠 system prompt 或 description 做授权。
10. 认证失败必须返回结构化错误，不能静默降级。

任何把 `Authorization` 直接从 request 转发到 Graph 的改动都是 P0。
任何把 `allowed_client_ids` 清空作为“先跑起来”的改动都是 P0。

## 7. Confirmation Review

文件：`src/m365_mcp/confirmation.py`。

按调用顺序检查：

```text
verify token
  -> check alg
  -> check signature
  -> check issuer/audience
  -> check exp/iat/max ttl
  -> check jti format
  -> check action
  -> check params_sha256
  -> check actor
  -> reserve jti
  -> execute upstream
```

必须验证：

1. `alg=none` 被拒绝。
2. 算法只接受配置的 RS256。
3. `audience` 不能跨 Server。
4. `params_sha256` 使用 canonical JSON。
5. actor 必须与入口 client 一致。
6. jti 只使用一次。
7. Redis 故障时失败关闭。
8. 上游调用之前 consume。
9. 写操作失败后不自动重试。
10. confirmation token 不进日志、不进响应的 `data`。

如果修改 canonicalization，必须同时更新 API 文档和所有测试向量。
如果新增多 key 或 `kid`，需要设计 overlapping rotation，不能只替换公钥。

## 8. MCP 契约 Review

文件：`src/m365_mcp/server_common.py`。

确认：

1. 五个治理工具仍然存在。
2. `search_actions` 默认只返回 enabled。
3. `describe_action` 不返回 backend 执行细节。
4. `execute_write` 的 annotation 是 destructive=true。
5. `execute_read` 是 readOnly=true。
6. 工具错误是结构化 JSON。
7. 所有异常都被转换为可诊断的 code。
8. 不在 tool description 里写行为操控指令。
9. 结果大小和 Inventory limit 有上限。
10. `/healthz` 和 `/readyz` 不泄漏 secret。

如果新增一个 MCP tool，必须单独 Review：
1. 它是否只是业务 action 的重复包装。
2. 它是否绕过了 search/execute 治理。
3. 它是否扩大 Agent 权限面。
4. 它是否有独立 annotation。
5. 它是否具有结构化输入 Schema。
6. 失败是否安全关闭。

## 9. 配置与部署 Review

重点看 `config.py`、`deploy/`、`docker-compose.yml`。

必须确认：

1. 非 root 用户 UID 10001。
2. rootfs read-only。
3. `cap_drop: ALL`。
4. `no-new-privileges`。
5. secrets 只读挂载。
6. config 只读挂载。
7. Valkey 只在 backend network。
8. Agent 不能访问 Valkey。
9. 上游证书和入口 token 分离。
10. 端口默认绑定 loopback。
11. PowerShell 7.6.9 和 module 版本固定。
12. PowerShell tarball 有 SHA-256。
13. `ExchangeOnlineManagement 3.10.1` 与 PowerShell 版本兼容。
14. 健康检查不依赖外部 tenant。
15. `/readyz` 能发现 Review 或 enabledTools 配置错误。

任何把 secrets 写进 `.env`、镜像、日志或 command argument 的改动都是 P0。
任何把 `MCP_ALLOWED_HOSTS` 或 TLS 校验关闭的改动都需要安全负责人批准。

## 10. 测试 Review

测试不是只看覆盖率数字。
必须检查测试是否真的覆盖失败路径和边界。

最低要求：

| 变更类型 | 必须新增或更新 |
|---|---|
| 新 Catalog action | Schema 拒绝额外字段、read/write、成功映射 |
| Graph endpoint | path encoding、错误、分页或结果上限 |
| PowerShell cmdlet | 参数映射、constant、timeout、worker 协议 |
| Auth | 错 audience、错 tenant、错 role、错 client |
| Confirmation | 错 action、错 params、错 actor、过期、重放 |
| Server tool | annotation、enabled gate、结构化错误 |
| Docker | Compose schema、非 root、read-only、health |

允许使用 fake upstream，但必须明确说明它不能替代 tenant 验收。
如果一个 PR 只加 happy path 测试，不给 disabled、非法参数、错误 token 的负向用例，应要求补充。

## 11. Review 记录模板

每个批准写动作或修改安全边界的 PR，应保留以下记录：

```text
Reviewer:
Date:
Commit / image digest:
Server:
Action(s):
Backend binding:
Permission delta:
Risk level:
Schema change:
Security invariants checked:
Test commands and result:
Tenant evidence:
Rollback tested:
Residual risk:
Decision: approve / reject / defer
```

Review registry 里的 `evidence` 不能只写“tested”。
应写 Change ID、工单、测试对象、权限结果和回滚证据。

## 12. P0/P1/P2 分级

### P0 阻断

- 任意 endpoint、任意 cmdlet 或任意脚本入口。
- 未 approved 或未 enabled 的 action 可执行。
- 写操作不需要 confirmation。
- confirmation consume 在上游调用之后。
- 入口 token 被转发到 Microsoft。
- 证书 password、Bearer token 或业务 payload 进入日志。
- action 默认 enabled。
- 允许任意 URL 的 nextLink。
- 允许 `alg=none`。
- Agent 提供的 embedding vector 被信任。
- semantic 结果绕过 enabled、Review 或 operation filter。
- 把 secrets 提交进 Git 或镜像。

### P1 必须合并前修

- Schema 允许额外字段。
- 高风险 action 被标为 low。
- permission 明显过宽。
- Graph write 有自动重试。
- jti 没有原子占用。
- 出错路径把写成功伪装成失败，且没有对账说明。
- 没有负向测试。
- Review registry 缺少 evidence。
- 生产配置依赖 `MCP_AUTH_MODE=none`。
- hybrid 权重和阈值没有任何评测集证据。
- embedding 模型或镜像没有 pin revision/digest。
- embedding 服务日志记录完整 query。

### P2 可跟进

- 错误信息可读性。
- 命名一致性。
- Inventory 描述和官方文档链接。
- 审计字段补充。
- 注释和 Runbook 更新。

P2 不能用来掩盖 P0。
如果安全不变量没满足，功能再完整也不能合并。

## 13. 建议阅读顺序

### 30 分钟快速 Review

1. 看 `models.py` 的 `GraphRequestSpec`、`PowerShellRequestSpec` 和 `ActionDefinition`。
2. 看 `catalog.py` 的 `require_action`、`is_enabled` 和 `validate_params`。
3. 看 `confirmation.py` 的 `verify_and_consume`。
4. 看 `search_lexicon.py` 的英文 normalization、concept 和 phrase 映射。
5. 看 `search_service.py` 的 initialize、semantic gate 和 hybrid 打分。
6. 看 `server_common.py` 的 `_register_tools`。
7. 看 `docker-compose.yml` 的 secrets、networks 和 read_only。
8. 跑 Ruff、pytest、inventory validate。

### 2 小时完整 Review

在上述基础上：

1. 对照 `scripts/generate_graph_catalog.py` 检查新增 Graph binding。
2. 检查 `graph/executor.py` 的 path/query/body。
3. 检查 `graph_client.py` 的 nextLink、retry 和大小上限。
4. 检查 `powershell/executor.py` 和 `worker.ps1` 的参数与进程模型。
5. 检查 `search_service.py` 的远程 embedding、index、fallback 和 readiness。
6. 检查 `config.py` 的 secret file 和启动失败路径。
7. 检查测试是否覆盖所有负向分支。
8. 检查 Dockerfile 的 module pin、版本和 checksum。
9. 检查 Review evidence 和 enabledTools diff。

### 安全边界变更

如果 PR 涉及下面的任一文件，必须走完整 Review：

```text
auth.py
confirmation.py
catalog.py
models.py
server_common.py
deploy/
docker-compose.yml
config/
```

涉及这些文件的 PR 不应只看代码风格。
必须明确写出“哪些不变量仍然成立，哪些测试证明了它”。

## 14. 当前已知的非代码缺陷

这些问题不是本指南的检查项，但生产验收必须知道：

1. 当前环境没有 Docker daemon，镜像尚未实际构建。
2. 当前没有真实 Microsoft 365 tenant，所有上游 API 权限尚未实际验证。
3. 69 个 Exchange remote cmdlet 仍需要 tenant session 验证参数和返回结构。
4. confirmation 当前只支持一个验证公钥，没有 `kid` 多 key rotation。
5. 一个部署只对应一个入口 tenant 和一个 upstream tenant。
6. 全部上游操作使用 app-only 身份，不提供 per-user delegated audit。
7. 当前只启用 159 个 read action，127 个 write action 仍关闭。
   这批 read action 使用 owner 批量批准，不是逐 action 的正式生产 Review。

这些问题应在发布评审中作为 residual risk 记录。
它们不能被“本地测试通过”掩盖。
