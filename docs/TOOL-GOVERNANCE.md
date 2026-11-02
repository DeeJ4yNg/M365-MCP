# Tool Inventory 与 Review 治理

## 1. 当前规模

当前统一 Inventory 共 298 个业务动作。

| Server | 动作数 | 已实现 | 计划 | 不支持 | Approved | 启用 |
|---|---:|---:|---:|---:|---:|---:|
| graph | 181 | 181 | 0 | 0 | 100 | 100 |
| powershell | 117 | 105 | 4 | 8 | 59 | 59 |
| 合计 | 298 | 286 | 4 | 8 | 159 | 159 |

当前基线只启用 159 个 implemented read action。
127 个 write action 和 12 个未实现 action 仍为 disabled。
这批 read action 使用 owner 批量批准，evidence 为 `OWNER-APPROVED: bulk read-only enablement for Agent onboarding (2026-11-02)`。
进入生产前应替换为逐 domain 的正式 Review 证据，并补做最小权限和 tenant 验收。
不要因为 read 已启用就默认所有上游 Graph/Exchange 权限已经具备。

领域分布：

| Domain | 动作数 |
|---|---:|
| Exchange Online | 69 |
| Intune | 64 |
| Entra ID | 45 |
| Microsoft Teams | 40 |
| SharePoint Online | 28 |
| Security & Compliance | 26 |
| Microsoft 365 | 19 |
| OneDrive | 7 |

读写与风险分布：

| 维度 | 数量 |
|---|---:|
| read | 161 |
| write | 137 |
| low risk | 108 |
| medium risk | 80 |
| high risk | 82 |
| critical risk | 28 |

这些数字来自 `uv run m365-inventory stats` 和 `inventory/M365-Tool-Inventory.json`。
修改 catalog 后必须重新生成 inventory，不能手工改报表数字。

## 2. Inventory 字段

每个动作包含以下字段：

| 字段 | 含义 |
|---|---|
| `name` | 稳定 Tool ID，Agent 调用的唯一业务动作标识 |
| `domain` | Entra ID、Intune、Exchange Online 等统一领域名 |
| `backend` | `graph` 或 `powershell` |
| `description` | 业务能力描述，会进入 Agent 发现结果 |
| `required_permissions` | Graph Application permission、Exchange 权限或 Entra role |
| `operation_type` | `read` 或 `write` |
| `risk_level` | `low`、`medium`、`high`、`critical` |
| `implementation_status` | `implemented`、`planned`、`unsupported` |
| `review_status` | `pending`、`approved`、`rejected` |
| `enabled` | 运行时是否可执行 |
| `keywords` | 搜索补充词 |
| `docs_url` | Microsoft Learn 或官方 PnP 文档 |
| `input_schema` | JSON Schema Draft 2020-12 子集 |
| `review` | reviewer、reviewed_at、evidence 等审计元数据 |

`backend` 请求绑定不出现在 MCP 返回中。
Agent 只能看到业务 Schema、权限、风险和状态，不能提交底层 endpoint 或 cmdlet。
后台执行细节由 `graph` 或 `powershell` 字段保存，属于服务端安全边界。

## 3. 状态语义

### 3.1 implementation_status

| 状态 | 含义 | 能否启用 |
|---|---|---|
| `implemented` | 声明式绑定和执行路径已实现，通过 Schema 和单元测试 | Review approved 后可启用 |
| `planned` | 能力有业务价值，但当前未完成绑定或官方支持不明确 | 不可启用 |
| `unsupported` | 官方 app-only 或当前后端明确不支持 | 不可启用 |

`implemented` 不代表已在真实 tenant 验证。
每个动作仍需要 tenant 权限验证、参数边界验证和回滚验证。

### 3.2 review_status

| 状态 | 含义 | 能否启用 |
|---|---|---|
| `pending` | 新动作默认状态 | 不可启用 |
| `approved` | 人工 Review 通过 | 还需要 enabledTools |
| `rejected` | 人工拒绝 | 不可启用 |

Review 结果存放在 `config/review-registry.json`，不写回镜像内 catalog。
这样升级镜像时不会覆盖人工决策，也可以在 Host 上审计变更。

### 3.3 enabled

`enabled` 是运行时派生值，不是 catalog 的静态字段。
它同时满足以下条件才为 true：

1. action 在对应 server 的 `enabledTools` 数组中。
2. `implementation_status=implemented`。
3. Review registry 中该 action 的状态为 `approved`。

三者缺一，动作不可执行。

## 4. 默认关闭

Catalog 校验明确禁止 `enabled=true` 写在静态 JSON 中。
新增动作必须默认：

```json
{
  "implementation_status": "implemented",
  "review_status": "pending",
  "enabled": false
}
```

启动时如果 enabledTools 包含未 approved 或未 implemented 动作，Server 失败关闭。
热加载时如果文件变成非法状态，下一次调用返回 `invalid_enabled_tools`。
Server 不会忽略错误继续以旧状态运行。

## 5. Review 流程

### 5.1 准备证据

Review 不应只写一句“看起来没问题”。
每个 approved 记录至少需要：

| 证据 | 示例 |
|---|---|
| Reviewer | 安全负责人或平台负责人 UPN |
| Evidence | Change ID、Jira、工单、测试报告链接或内部记录 ID |
| 测试 tenant | 隔离 tenant 或指定测试对象 |
| 权限验证 | 实际 app-only token 权限与授权结果 |
| 正向测试 | 合法参数成功 |
| 负向测试 | 越权、越界、额外参数、错误 ID 被拒绝 |
| 回滚方案 | 写动作的撤销步骤 |
| 数据范围 | 全 tenant、指定 site、指定 mailbox 或测试组 |

### 5.2 批准但先不启用

```bash
uv run m365-inventory review \
  --server graph \
  --action entra_list_users \
  --status approved \
  --reviewer security-reviewer@example.com \
  --evidence CHG-2026-0001
```

如果只执行 review 而没有把 action 加入 enabledTools，它仍不可执行。
当前 read 基线已经批量加入 enabledTools，write action 仍保持未启用。

### 5.3 启用

```bash
uv run m365-inventory enable --server graph --action entra_list_users
```

CLI 会再次检查：
1. action 存在。
2. Review 已 approved。
3. implementation 为 implemented。
4. enabledTools 文件可写。
5. 写入采用临时文件加 rename。

### 5.4 停用

```bash
uv run m365-inventory disable --server graph --action entra_list_users
```

停用不需要删除 Review。
下次重新启用时仍可复用已有 Review 证据。
如果证据已过期或权限发生变化，应先重新 Review。

### 5.5 拒绝

```bash
uv run m365-inventory review \
  --server powershell \
  --action wipe_mobile_device \
  --status rejected \
  --reviewer security-reviewer@example.com \
  --evidence SEC-2026-0042
```

即使 enabledTools 仍包含该动作，Review rejected 会让策略检查失败。
下一次调用会失败关闭，直到配置被修正。
生产变更流程应在拒绝后立即执行 `disable` 并验证 `/readyz`。

## 6. 验证与导出

```bash
uv run m365-inventory validate
uv run m365-inventory stats
uv run m365-inventory export --output inventory
```

`validate` 校验 catalog 模型、Review registry 和 enabledTools。
`stats` 输出按 Server、domain、状态和风险统计。
`export` 生成：

```text
inventory/M365-Tool-Inventory.json
inventory/M365-Tool-Inventory.csv
inventory/M365-Tool-Inventory.md
```

生成的 Markdown 明确标记为自动生成。
不要手工编辑生成文件。
修改 catalog 或 Review 状态后重新运行 export。

## 7. enabledTools 文件格式

```json
{
  "schemaVersion": 1,
  "enabledTools": {
    "graph": [
      "entra_list_users",
      "m365_list_service_health"
    ],
    "powershell": [
      "get_exchange_mailbox"
    ]
  }
}
```

规则：

1. 顶层必须是对象。
2. 只允许 `graph` 和 `powershell` 两个 server key。
3. 每个值是 action name 数组。
4. 不允许重复项。
5. 不允许未知 server key。
6. 不允许未知 action name。
7. 不允许未 approved 或未 implemented 的 action。

Graph Server 只读取 `graph` 数组。
PowerShell Server 只读取 `powershell` 数组。
两个 Server 共用同一个挂载文件，但各自校验自己的列表。
配置更新通过 mtime 热加载，无需重启。

## 8. Review registry 文件格式

```json
{
  "schemaVersion": 1,
  "reviews": {
    "graph": {
      "entra_list_users": {
        "status": "approved",
        "reviewed_by": "security-reviewer@example.com",
        "reviewed_at": "2026-10-31T12:00:00Z",
        "evidence": "CHG-2026-0001"
      }
    },
    "powershell": {}
  }
}
```

`approved` 和 `rejected` 必须有 reviewer、ISO 8601 时间和 evidence。
缺少任何字段都会导致 registry 校验失败。
registry 修改也会 mtime 热加载。
registry 文件不可由容器自动生成 approved 记录。

## 9. 风险等级建议

| Risk | 典型动作 | 运行要求 |
|---|---|---|
| low | 列表、详情、报告读取 | 只读 role，仍要 Review |
| medium | 邮箱自动回复、隐藏地址、audit 查询、service health | 指定对象范围，记录变更 |
| high | 授权、转发、条件访问写入、设备 reboot、共享链接 | 二次人工审批，验证回滚 |
| critical | 删除、wipe、角色分配、条件访问删除、设备擦除 | Change 审批、目标白名单、随叫随停 |

风险不是自动执行许可。
任何 write 都必须经过 confirmation token。
critical 动作还应在审批系统里要求更高等级的审批人。

## 10. 审计与可追溯性

每次 `execute_read` 和 `execute_write` 都会产生结构化审计事件：

```json
{
  "event": "mcp_tool_execution",
  "server": "graph",
  "action": "entra_list_users",
  "operation": "read",
  "risk": "low",
  "actor": "agent-client-id",
  "correlation_id": "uuid",
  "outcome": "success",
  "duration_ms": 42.1
}
```

写操作额外记录：

```json
{
  "approver": "CHG-2026-0001",
  "reason": "short business reason"
}
```

审计不记录参数内容。
如果需要追踪具体参数，应在审批系统中保存 canonical 参数和 `params_sha256`。
Correlation ID 同时发送到 Microsoft Graph `client-request-id`。
排查时可以要求 Graph 支持请求 ID 与 MCP correlation ID 对齐。

## 11. 变更控制

新增动作的默认状态链：

```text
catalog proposed
  -> implementation_status=implemented, review_status=pending, enabled=false
  -> tests pass
  -> security review
  -> review_status=approved with evidence
  -> enabledTools add
  -> canary tenant validation
  -> production enabled
```

删除动作的默认处理：

1. 先从 enabledTools 移除。
2. 验证没有 Agent workflow 依赖。
3. 标记 review rejected 或从 catalog 删除。
4. 重新生成 inventory。
5. 发布新镜像。
6. 保留旧审计日志和审批记录。

不要在仍启用时直接删除 catalog 动作。
Server 启动时会检测到 enabledTools 未知 action 并失败关闭。
