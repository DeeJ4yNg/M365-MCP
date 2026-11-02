# 新增业务 Tool 开发说明

本项目的业务动作不是散落在 Python 函数里的临时 endpoint。
每个 Graph 和 PowerShell 动作都通过声明式 Catalog 定义，再由统一执行器分发。
新增工具的核心工作是：定义固定后端绑定、定义严格 Schema、补充权限和测试、重新生成 Inventory、完成 Review。
不要新增 `generic_graph_request`、`run_powershell`、`invoke_cmdlet` 一类自由执行入口。

## 1. 先判断后端

| 问题 | 选择 |
|---|---|
| Microsoft Graph v1.0 是否已有稳定 endpoint 和 Application permission | Graph |
| 能力只在 Exchange Online PowerShell 中 | PowerShell `exchange` |
| 能力只在 Security & Compliance PowerShell 中 | PowerShell `compliance` |
| 能力只在 Teams PowerShell 中 | PowerShell `teams` |
| 能力只在 SharePoint tenant admin / PnP 中 | PowerShell `sharepoint` |
| 只有 beta endpoint | 默认不加入 |
| 官方 app-only 明确不支持 | 加入 Catalog 时标 `unsupported`，不要实现假绑定 |

优先使用 Graph。
只有 Graph 没有稳定覆盖时才使用 PowerShell。
不要因为已有 PowerShell 模块就把 Graph 已有能力重复包装。
重复工具会增加权限面、测试面和 Agent 选择歧义。

## 2. Graph Tool

### 2.1 修改源定义

Graph Catalog 的唯一来源是：

```text
scripts/generate_graph_catalog.py
```

不要直接编辑 `src/m365_mcp/catalogs/graph.json`。
新增动作时在对应 domain 函数中添加 `read()` 或 `write()` 定义。

只读示例：

```python
read(
    name="entra_list_users",
    domain="Entra ID",
    description="List tenant users with optional server-side filtering and field selection.",
    permissions=["User.Read.All"],
    path="/users",
    docs="https://learn.microsoft.com/en-us/graph/api/user-list",
    params=page_props(),
    query={"$top": "limit", "$filter": "filter", "$select": "select"},
    keywords=["user", "account", "list"],
)
```

写操作示例：

```python
write(
    name="entra_update_user",
    domain="Entra ID",
    description="Update selected properties on an existing user.",
    permissions=["User.ReadWrite.All"],
    path="/users/{user_id}",
    method="PATCH",
    docs="https://learn.microsoft.com/en-us/graph/api/user-update",
    params={
        "user_id": prop("string", "User object ID or UPN.", minLength=1, maxLength=320),
        "payload": obj("Graph user patch payload."),
    },
    required=["user_id", "payload"],
    body={"payload": "payload"},
    risk="medium",
    keywords=["user", "update"],
)
```

### 2.2 命名

Tool name 必须：

1. snake_case。
2. 以领域或资源开头，例如 `entra_`、`intune_`、`sharepoint_`。
3. 小于等于 64 字符。
4. 动词在后，例如 `entra_get_user`、`intune_wipe_device`。
5. 读写动作使用不同名称。
6. 不把 HTTP method 放进名称。

### 2.3 Path

Path 必须是 Microsoft Graph v1.0 相对路径。
Path 只能使用 `{param}` 占位。
占位参数必须在 `required` 中。
不能包含 query string、fragment、双斜杠或完整 URL。
Graph 执行器会对占位值做 URL encode。
不要在 path 中拼用户提供的原始路径段。

### 2.4 Query

`query` 映射 Graph query 参数到业务参数名：

```python
query = {"$top": "limit", "$select": "select"}
```

只能使用 endpoint 确实支持的 OData 参数。
不要提供 `query` 字符串让调用方拼接 OData。
`$select` 使用 string array，执行器会转成逗号分隔。
`$filter` 是受支持 endpoint 的字符串参数，必须限制长度。

### 2.5 Body

简单字段映射：

```python
body = {
    "displayName": "display_name",
    "accountEnabled": "account_enabled",
}
```

固定常量加映射：

```python
body = ({"roles": "role"},)
body_const = {
    "@odata.type": "#microsoft.graph.aadUserConversationMember",
    "user@odata.bind": "https://graph.microsoft.com/v1.0/users('{user_id}')",
}
```

`body_const` 中只能引用 Schema 中 required 的占位参数。
执行器会替换占位符，JSON 序列化会处理转义。
不要把任意 JSON 字符串解释成模板语言。

复杂 typed payload：

```python
params = {"payload": obj("Typed Intune policy payload.")}
required = ["payload"]
body = {"payload": "payload"}
```

这种模式仍绑定固定 endpoint，只是允许完整 typed body。
描述必须链接官方文档，方便 Review 和 Agent 理解 payload。

### 2.6 Schema

所有 Schema 必须：

1. `type=object`。
2. `additionalProperties=false`。
3. 每个 property 都有 `description`。
4. 字符串有合理 `minLength` 和 `maxLength`。
5. 枚举使用 `enum`。
6. 数字设置 `minimum` 和 `maximum`。
7. 日期使用 `format=date-time`。
8. 数组设置 `items` 和 `maxItems`。
9. ID 参数不允许空字符串。
10. 不在 Schema 中放执行指令。

Registry 的 `Draft202012Validator` 会拒绝额外字段和越界值。
服务端不会静默修正非法输入。

### 2.7 权限

`required_permissions` 必须列出最小 Application permission。
不要复制 delegated scope。
不要写“Admin”这种无法在 Entra 中定位的模糊词。
如果多个 endpoint 共享一个动作，拆成多个动作，不要在一个工具里根据参数切换 endpoint。

### 2.8 风险

| 条件 | 最低风险 |
|---|---|
| 读取元数据、列表、报告 | low |
| 读取可能包含个人数据或邮件内容 | medium |
| 修改配置、权限、共享 | high |
| 删除、wipe、角色分配、Conditional Access 删除 | critical |

风险是审批要求，不是自动执行条件。

### 2.9 生成和验证

```bash
uv run python scripts/generate_graph_catalog.py
uv run m365-inventory validate --server graph
uv run m365-inventory stats --server graph
uv run pytest tests/test_catalog.py tests/test_graph_executor.py
```

生成脚本会在写文件前校验整个 Catalog。
出现重复名称、缺 Schema、错误绑定、未知占位参数时会失败。
不要绕过生成脚本手工修复生成的 JSON。

## 3. PowerShell Tool

### 3.1 修改 Catalog

PowerShell Catalog 位于：

```text
src/m365_mcp/catalogs/powershell.json
```

每个动作必须有固定的 `service` 和 `cmdlet`。
`service` 只能是：

```text
exchange
compliance
teams
sharepoint
```

只读示例：

```json
{
  "name": "get_exchange_mailbox",
  "domain": "Exchange Online",
  "backend": "powershell",
  "description": "读取指定 Exchange Online 邮箱属性。",
  "required_permissions": [
    "Exchange.ManageAsApp",
    "Exchange Administrator role"
  ],
  "operation_type": "read",
  "risk_level": "low",
  "implementation_status": "implemented",
  "review_status": "pending",
  "enabled": false,
  "keywords": ["exchange", "mailbox"],
  "docs_url": "https://learn.microsoft.com/en-us/powershell/module/exchange/get-exomailbox",
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "identity": {
        "type": "string",
        "minLength": 1,
        "maxLength": 320,
        "description": "邮箱 Identity。"
      }
    },
    "required": ["identity"]
  },
  "powershell": {
    "service": "exchange",
    "cmdlet": "Get-EXOMailbox",
    "parameters": {
      "identity": "Identity"
    }
  }
}
```

### 3.2 参数映射

`parameters` 是业务参数名到 PowerShell 参数名的白名单映射。
只映射通过 Schema 校验的字段。
`constant_parameters` 用于固定值，例如 `Confirm=false`。
调用方不能在 params 中覆盖 constant。
未在 `parameters` 中声明的参数会被 worker 拒绝。

### 3.3 命令安全

禁止：

```text
Invoke-Expression
Invoke-Command
Invoke-RestMethod
Invoke-WebRequest
Start-Process
New-Object
Add-Type
Set-ExecutionPolicy
Import-Module
Remove-Item
```

禁止把用户输入拼接成 PowerShell 脚本。
禁止在一个动作用 `Invoke-Expression` 动态组合 cmdlet。
禁止接受任意 cmdlet 名称。
PowerShell worker 只执行 Python 端传来的固定 action 绑定。

### 3.4 读写分类

`Get-`、`Search-`、`Preview-` 默认必须标 read。
`Remove-`、`Clear-` 必须标 write。
`Set-`、`New-`、`Add-`、`Enable-`、`Disable-`、`Grant-` 等应标 write。
不要把查询型 cmdlet 包在 write 里绕过审批。
不要把 wipe 标成 medium。

### 3.5 连接

每次调用启动新 `pwsh`。
worker 根据 service 连接：

| service | 连接方式 |
|---|---|
| exchange | `Connect-ExchangeOnline` certificate file |
| compliance | `Connect-IPPSSession` certificate file |
| teams | `Connect-MicrosoftTeams` certificate object |
| sharepoint | `Connect-PnPOnline` certificate file |

连接参数来自容器环境或 Docker secret。
不要把证书 password 写入 Catalog。
不要让 PowerShell 命令从调用方参数读取连接信息。

### 3.6 超时和并发

默认 worker timeout 120 秒。
默认并发 4。
大型 mailbox、message trace、PnP tenant site 查询可能超过默认时间。
优先缩小查询。
如果必须提高 timeout，在 Compose 和运维文档中记录原因和资源影响。
不要在 worker 中无限等待。

### 3.7 测试

```bash
uv run pytest tests/test_catalog.py tests/test_powershell_executor.py
uv run m365-inventory validate --server powershell
```

测试必须覆盖：

1. Catalog 模型通过。
2. cmdlet 白名单通过。
3. 参数映射正确。
4. constant 不能被覆盖。
5. 超时能杀进程。
6. worker 输出格式错误时不当作成功。
7. 大结果不会无界返回。
8. Read/Write 分类正确。

真实 PowerShell 模块行为需要在隔离 tenant 测试。
本地 fake pwsh 测试不能替代 tenant 验收。

## 4. 新增 Tool 的 MCP 面

普通业务动作不需要修改 `server_common.py`。
两个 Server 的五个治理工具会自动读取 Catalog。
如果确实要新增一个治理工具，例如 `audit_tool_usage`，需要：

1. 修改 `server_common.py`。
2. 加 MCP title、readOnlyHint、destructiveHint、idempotentHint、openWorldHint。
3. 添加独立鉴权。
4. 添加结构化错误。
5. 添加输入 Schema。
6. 更新 API 文档和测试。
7. 重新走权限 Review。

当前五个治理工具是固定 MCP 公共面。
新增治理工具属于安全边界变更，必须重新 Review，不能按普通业务动作自动启用。

## 5. 完整的 PR 检查清单

提交前逐项确认：

- [ ] 动作有唯一名称且不超过 64 字符。
- [ ] Domain 使用统一名称。
- [ ] Backend 与固定绑定一致。
- [ ] 描述说明能做什么、不能做什么。
- [ ] 官方文档链接可访问。
- [ ] Schema 拒绝额外字段。
- [ ] 路径或 cmdlet 不接受任意用户输入。
- [ ] 读和写分离。
- [ ] 风险等级合理。
- [ ] required_permissions 使用准确名称。
- [ ] implemented 动作通过单元测试。
- [ ] planned/unsupported 动作没有伪造绑定。
- [ ] 新增或更新 Tool Inventory。
- [ ] `uv run pytest` 全通过。
- [ ] `uv run ruff check .` 全通过。
- [ ] `uv run ruff format --check .` 全通过。
- [ ] 没有把任何动作默认启用。
- [ ] 隔离 tenant 的验证证据已进入 Review。
- [ ] 更新本文档或相关 Runbook。

## 6. Review 后的启用

```bash
uv run m365-inventory review \
  --server graph \
  --action your_new_action \
  --status approved \
  --reviewer reviewer@example.com \
  --evidence CHG-2026-0001

uv run m365-inventory enable --server graph --action your_new_action
uv run m365-inventory export --output inventory
```

Server 会在下一次调用时热加载 enabledTools。
`readyz` 会显示 enabled 数量。
在真实 Agent 里先执行只读动作，再进入写动作。
如果动作涉及权限变更，先在测试对象上确认回滚路径。

## 7. 不要做的事

1. 不要为了少写几个 Schema 提供自由 JSON `params` 给任意 endpoint。
2. 不要把 HTTP method 做成调用参数。
3. 不要新增 `run_any_cmdlet`。
4. 不要用 `Invoke-Expression` 加速开发。
5. 不要把 beta endpoint 混入 v1.0 Catalog 而不标注。
6. 不要把 delegated 权限当成 app-only 验证。
7. 不要在审批系统之外自动生成 approval token。
8. 不要让 enabledTools 自动包含全部 implemented 动作。
9. 不要手工编辑生成的 Inventory。
10. 不要把真实业务 payload 写入普通运行日志。
