# MCP 接口契约

## 1. Transport

| 项目 | 值 |
|---|---|
| MCP transport | Streamable HTTP |
| MCP path | `/mcp` |
| 模式 | stateless |
| Graph endpoint | `http://graph-mcp:8000/mcp` |
| PowerShell endpoint | `http://powershell-mcp:8001/mcp` |
| 入口认证 | Entra app-only JWT Bearer |
| 上游认证 | certificate app-only，独立于入口 token |
| 健康检查 | `/healthz` |
| 就绪检查 | `/readyz` |

请求头：

```http
Authorization: Bearer <token>
Content-Type: application/json
Accept: application/json, text/event-stream
```

不同 MCP host 的注册方式不同。
本项目不依赖某个特定 Agent framework。
只要客户端支持 Streamable HTTP 和 Bearer header 即可接入。
所有 HTTP 响应都带 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff` 和 `Referrer-Policy: no-referrer`。

## 2. 入口授权

MCP Server 校验：

1. JWT 签名来自配置 tenant 的 JWKS。
2. issuer 匹配。
3. audience 匹配对应 Server 的 Application ID URI。
4. token 未过期。
5. `roles` 包含读或写 app role。
6. 调用方 client ID 在 allow-list 中。
7. token tenant 匹配配置 tenant。

读工具要求：

```text
M365.MCP.Read
或
M365.MCP.ReadWrite
```

写工具要求：

```text
M365.MCP.ReadWrite
```

`roles` 是 Application app role claim。
不要依赖 `scp` 判断 app-only 写权限。

## 3. 公共工具

### 3.1 search_actions

签名：

```json
{
  "intent": "find inactive users",
  "limit": 10,
  "domain": "Entra ID",
  "operation_type": "read",
  "include_disabled": false
}
```

参数：

| 参数 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `intent` | string | 是 | 非空自然语言意图 |
| `limit` | integer | 否 | 1 到 20，默认 10 |
| `domain` | string | 否 | domain 名或常用别名，例如 Entra、Intune、Exchange、Teams、终端 |
| `operation_type` | string | 否 | `read` 或 `write` |
| `include_disabled` | boolean | 否 | 默认 false |

English ticket terms 可以直接作为 `intent` 使用，例如 `user cannot sign in after MFA reset`、`remove user from DL`、`release quarantined email`。
MCP 会做 phrase normalization 和 concept expansion，不需要调用方先翻译成 command 名称。

默认只返回 enabled 动作。
`include_disabled=true` 用于盘点、排障和候选评审，不代表动作可执行。
每个 Server 只搜索自己的 Catalog，Graph MCP 不会返回 PowerShell action。
同时连接两个 Server 时，Agent 需要分别搜索两边。
搜索支持 `lexical` 和 `hybrid` 两种模式。
英文是主语言，词法层会做 CamelCase 拆分、产品短语归一、英文形态变体和 Microsoft 365 concept 扩展。
中文作为双语补充，通过 concept aliases 映射到同一套英文动作词汇。
Hybrid 会调用部署在 DGX Spark 上的私有 embedding 服务，不调用 Microsoft Graph，也不调用公网 embedding API。
没有足够相关度时返回空列表，不会用无关动作填充结果。

返回：

```json
{
  "actions": [
    {
      "name": "entra_list_users",
      "title": "Entra List Users",
      "domain": "Entra ID",
      "backend": "graph",
      "description": "List tenant users...",
      "required_permissions": ["User.Read.All"],
      "operation_type": "read",
      "risk_level": "low",
      "implementation_status": "implemented",
      "review_status": "approved",
      "enabled": true,
      "input_schema": {},
      "score": 0.87
    }
  ],
  "count": 1,
  "enabled_actions": 6,
  "search_mode": "hybrid",
  "semantic_ready": true,
  "warning": null,
  "hint": "Refine the intent or inspect the returned candidates."
}
```

`score` 的尺度取决于 `search_mode`：

| mode | score |
|---|---|
| `lexical` | 原始词法分，未归一化 |
| `hybrid` | 0 到 1 的 hybrid 分 |
| `lexical_fallback` | 原始词法分，并带 warning |

`lexical_fallback` 表示 semantic index 或 query embedding 不可用。
Agent 不应把 fallback 当成正常 hybrid 结果。

### 3.2 describe_action

签名：

```json
{
  "action_id": "entra_list_users"
}
```

返回完整 Inventory 元数据和 `input_schema`。
未知 action 返回 `action_not_found`。
动作被禁用时返回 `disabled_reason`。
这个工具只读，不执行任何上游调用。

### 3.3 get_tool_inventory

签名：

```json
{
  "limit": 50,
  "include_disabled": true,
  "domain": "Intune"
}
```

参数：

| 参数 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `limit` | integer | 否 | 1 到 200，默认 50 |
| `include_disabled` | boolean | 否 | 默认 true |
| `domain` | string | 否 | domain 名或常用别名 |

返回当前 Server 的 Inventory 快照、状态计数和版本。
它不执行业务动作。

## 4. execute_read

签名：

```json
{
  "action_id": "entra_list_users",
  "params": {
    "limit": 25,
    "filter": "accountEnabled eq true"
  }
}
```

规则：

1. action 必须存在。
2. operation 必须为 read。
3. 必须 implemented。
4. 必须 approved。
5. 必须在 enabledTools 中。
6. params 必须通过 action 的 JSON Schema。
7. 不得提交额外字段。
8. 调用方必须具有读角色。

成功返回：

```json
{
  "ok": true,
  "correlation_id": "uuid",
  "action": "entra_list_users",
  "data": {
    "value": []
  },
  "meta": {
    "status_code": 200,
    "request_id": "graph-request-id",
    "pages": 1,
    "truncated": false,
    "next_link": null,
    "content_type": "application/json"
  }
}
```

## 5. execute_write

签名：

```json
{
  "action_id": "entra_update_user",
  "params": {
    "user_id": "uuid",
    "payload": {
      "displayName": "Alice"
    }
  },
  "confirmation_token": "<signed-jwt>",
  "reason": "CHG-2026-0001 approved rename"
}
```

规则：

1. action 必须存在。
2. operation 必须为 write。
3. 必须 implemented。
4. 必须 approved。
5. 必须在 enabledTools 中。
6. params 必须通过 action 的 JSON Schema。
7. 调用方必须具有 `M365.MCP.ReadWrite`。
8. confirmation 签名、issuer、audience、action、params hash、actor 和 exp 必须匹配。
9. jti 必须未被占用。
10. 上游写调用不自动重试。

成功返回额外包含：

```json
{
  "ok": true,
  "correlation_id": "uuid",
  "approval": {
    "approver": "CHG-2026-0001",
    "single_use": true
  },
  "action": "entra_update_user",
  "data": {}
}
```

`approval.approver` 来自 confirmation token 的 `sub`。
审批 token 不会返回给 Agent 或写入日志。

## 6. Confirmation token claims

必须具备：

```json
{
  "iss": "m365-change-approval",
  "aud": "m365-graph-mcp",
  "sub": "CHG-2026-0001",
  "iat": 1790000000,
  "nbf": 1790000000,
  "exp": 1790000300,
  "jti": "graph:entra_update_user:random",
  "action": "entra_update_user",
  "params_sha256": "hex",
  "actor": "agent-client-id"
}
```

约束：

| Claim | 约束 |
|---|---|
| `iss` | 必须等于 `MCP_CONFIRMATION_ISSUER` |
| `aud` | Graph 为 `m365-graph-mcp`，PowerShell 为 `m365-powershell-mcp` |
| `sub` | 审批人或 change record ID，会进入审计 |
| `exp` | 必须未过期，且 `exp-iat` 不超过 `MCP_CONFIRMATION_MAX_TTL_SECONDS`，默认 900 秒 |
| `jti` | 8 到 128 字符，单次使用 |
| `action` | 必须与 execute_write 完全一致 |
| `params_sha256` | canonical JSON 参数的 SHA-256 |
| `actor` | 必须等于入口 JWT 的稳定 actor |
| `iat` | 必须存在 |
| `nbf` | 必须存在 |

canonical JSON 规则：

```text
JSON keys sorted
no insignificant whitespace
UTF-8
ensure_ascii=false
```

Python 计算示例：

```python
json.dumps(
    params,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

不要先重新序列化 payload 再计算 digest，否则字典顺序、Unicode 或数字类型可能变化。

## 7. 错误格式

工具错误是 MCP `isError=true`，文本内容为 JSON：

```json
{
  "code": "invalid_parameters",
  "message": "Parameters for action do not match its input schema",
  "details": {
    "validation_errors": [
      {
        "path": "$",
        "message": "Additional properties are not allowed"
      }
    ]
  },
  "retryable": false,
  "correlation_id": "uuid"
}
```

常见错误码：

| Code | 含义 | 可恢复建议 |
|---|---|---|
| `action_not_found` | 动作不存在 | 用 search_actions 查找候选 |
| `operation_mismatch` | 读动作走了写入口或反之 | 调用正确 execute 工具 |
| `action_disabled` | 未 Review 或未 enabled | 走治理流程，不要重试 |
| `invalid_parameters` | Schema 校验失败 | 按 details 修正参数 |
| `graph_request_failed` | Microsoft Graph 返回错误 | 检查权限、参数、节流 |
| `graph_unavailable` | 网络或 5xx | 只读可重试，写操作先对账 |
| `confirmation_required` | 缺少审批 token | 获取审批 |
| `confirmation_invalid` | token 无效、过期或 audience 错误 | 重新审批 |
| `confirmation_params_mismatch` | 参数摘要不匹配 | 重新审批完整参数 |
| `confirmation_actor_mismatch` | 调用的 Agent 与审批 actor 不一致 | 使用正确 Agent 或重批 |
| `confirmation_replayed` | jti 已使用 | 重新审批 |
| `confirmation_unavailable` | MCP 未配置验证公钥 | 运维修复 |
| `powershell_timeout` | cmdlet 超时 | 缩小查询或调整运维参数 |
| `powershell_protocol_error` | worker 返回无效协议 | 检查镜像和 worker 日志 |
| `embedding_unavailable` | semantic index 或 embedding 服务不可用 | 重启 MCP 或修复 embedding 服务 |
| `embedding_request_failed` | embedding endpoint 返回 4xx/5xx | 检查模型、认证和服务日志 |
| `embedding_invalid_response` | 向量数量、索引或格式不合法 | 检查兼容 API 和模型 |
| `embedding_dimension_mismatch` | query/action vector 维度不一致 | 清空索引并重新生成 |
| `embedding_model_mismatch` | 服务加载的模型与配置不一致 | 统一下发模型 revision |
| `embedding_vector_invalid` | 零向量或非有限值 | 检查模型输出 |
| `embedding_response_too_large` | 向量响应超过 10 MB | 降低 batch 或检查服务异常 |
| `result_too_large` | 结果超过上限 | 缩小 filter、select 或页大小 |

错误响应不包含 upstream Bearer token 或 certificate password。

## 8. 分页和结果上限

Graph GET 默认最多读取 5 页。
`MCP_MAX_PAGES` 可调整。
Graph `nextLink` 必须同源且位于 `/v1.0` 路径。
返回的 `meta.next_link` 可用于诊断，但业务工具不提供任意 URL cursor 参数。

服务端默认最大结果 1 MB。
这是序列化后的完整响应大小，不是单个 item 大小。
超过上限的读操作返回 `result_too_large`。
超过上限的写操作返回成功摘要并设置 `response_omitted=true`，不会把已经完成的写伪装成失败。

## 9. PowerShell 返回

PowerShell worker 始终返回数组：

```json
{
  "ok": true,
  "result": [
    {
      "Identity": "alice@contoso.com"
    }
  ]
}
```

Python 执行器最终返回：

```json
{
  "ok": true,
  "action": "get_exchange_mailbox",
  "data": [
    {
      "Identity": "alice@contoso.com"
    }
  ],
  "meta": {
    "service": "exchange",
    "cmdlet": "Get-EXOMailbox",
    "result_count": 1
  },
  "correlation_id": "uuid"
}
```

调用方不要解析 `cmdlet` 为可执行入口。
它只是响应诊断字段。

## 10. Health endpoints

`GET /healthz`：

```json
{
  "status": "ok",
  "server": "graph"
}
```

`GET /readyz`：

```json
{
  "status": "ready",
  "server": "graph",
  "version": "0.2.0",
  "checks": {
    "catalog": true,
    "enabledTools": true,
    "reviewRegistry": true,
    "writeConfirmation": true,
    "enabled_count": 6,
    "write_ready": true,
    "searchConfiguredMode": "hybrid",
    "searchMode": "hybrid",
    "semanticReady": true,
    "semanticActions": 181,
    "semanticDimension": 1024,
    "searchLexiconVersion": "2026-11-01",
    "searchAvailable": true
  }
}
```

`readyz` 返回 200 只代表 MCP 配置可服务。
它不逐个探测 Microsoft Graph、Exchange Online、Teams 或 SharePoint 上游。
配置了 `MCP_SEARCH_EMBEDDING_HEALTH_URL` 时，`readyz` 会探测 embedding 服务的健康端点。
如果 hybrid 配置了 fallback，embedding 不可用时返回 200 且 `status=degraded`，同时 `searchMode=lexical_fallback`。
如果 hybrid 未配置 fallback，embedding 不可用时返回 503。

## 11. 版本

当前 Server version 为 `0.2.0`。
MCP 初始化和 `get_tool_inventory` 都返回版本。
Catalog 变更、Schema 变更、认证行为变更和 confirmation claim 变更都必须提升版本。
Agent 不应硬编码 Inventory 总数量或 action 列表。
Agent 应通过 `search_actions` 和 `describe_action` 在运行时发现。
