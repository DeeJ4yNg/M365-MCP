# Entra ID 与权限配置

本页区分四条完全独立的身份链，不要复用同一个应用注册或同一张证书。
所有 Application permission 都需要管理员同意。
所有 app role 都必须分配到 Agent 应用的服务主体，而不是只写到 manifest。
建议在隔离 tenant 完成配置和验收后，再复制到生产 tenant。

## 1. 身份清单

| 身份 | 类型 | 用途 | 需要的 secret |
|---|---|---|---|
| `m365-graph-upstream` | Confidential client | Graph MCP 调用 Microsoft Graph | 证书 |
| `m365-powershell-upstream` | Confidential client | PowerShell MCP 连接 EXO、PnP、Teams | 证书 |
| `m365-graph-mcp-api` | Resource API | Graph MCP 的 audience 与 app roles | 无 |
| `m365-powershell-mcp-api` | Resource API | PowerShell MCP 的 audience 与 app roles | 无 |
| `enterprise-it-agent` | Confidential client | Agent Runtime 获取入口 JWT | 证书或受控凭据 |

两个 upstream 应用不能共用一个证书。
两个 MCP API 应用不持有 secret，它们只定义 audience 和 app role。
Agent 应用不持有 Graph 或 Exchange 权限，它只被授予 MCP API 的 app role。

## 2. 生成证书

在 Linux Host 上执行：

```bash
./scripts/create-service-certificate.sh graph
./scripts/create-service-certificate.sh powershell
./scripts/create-confirmation-keypair.sh
```

脚本生成：

```text
secrets/graph.key
secrets/graph.crt
secrets/graph.pfx
secrets/graph.pfx.password
secrets/powershell.key
secrets/powershell.crt
secrets/powershell.pfx
secrets/powershell.pfx.password
secrets/confirmation_signing.pem
secrets/confirmation_public.pem
```

只把 `.crt` 上传到对应的 Entra 应用注册。
把 `.key`、`.pfx`、`.pfx.password` 和 `confirmation_signing.pem` 放入企业 secret manager。
MCP 容器只挂载 `.pfx`、对应 password 文件和 `confirmation_public.pem`。
私钥文件权限必须是 `0600`，目录权限必须是 `0700`。
示例使用自签名证书，生产环境可使用企业 PKI 或受信任 CA 签发的新证书。
证书 subject 不影响 client credential，关键是私钥和 Entra 应用上的公钥指纹一致。

## 3. Graph upstream 应用

### 3.1 基础配置

1. 在 Entra admin center 创建应用 `m365-graph-upstream`。
2. 在 Certificates & secrets 上传 `secrets/graph.crt`。
3. 记录 Application (client) ID，写入 `.env` 的 `M365_GRAPH_CLIENT_ID`。
4. 不创建 client secret。
5. 在 API permissions 添加 Microsoft Graph Application permissions。
6. 完成 Grant admin consent。
7. 不需要为这个应用分配 Entra directory role，Graph 权限本身已由 app permission 表达。

### 3.2 只读基线权限

只启用只读动作时，先授予以下 Application permissions：

| 权限 | 覆盖 |
|---|---|
| `User.Read.All` | 用户、许可证详情 |
| `Group.Read.All` | 组读取 |
| `GroupMember.Read.All` | 组成员和所有者读取 |
| `Directory.Read.All` | 目录角色读取 |
| `Domain.Read.All` | 域名读取 |
| `Organization.Read.All` | 组织、订阅 SKU |
| `AuditLog.Read.All` | sign-in 与 directory audit |
| `UserAuthenticationMethod.Read.All` | MFA 方法读取 |
| `Reports.Read.All` | MFA 注册报告与 usage reports |
| `Policy.Read.All` | Conditional Access 读取 |
| `Application.Read.All` | 应用与 service principal 读取 |
| `RoleManagement.Read.Directory` | 角色定义和活动分配读取 |
| `Device.Read.All` | Entra 设备读取 |
| `DeviceManagementManagedDevices.Read.All` | Intune 设备读取 |
| `DeviceManagementConfiguration.Read.All` | 合规、配置和更新策略读取 |
| `DeviceManagementApps.Read.All` | Intune 应用读取 |
| `DeviceManagementServiceConfig.Read.All` | 注册与 Autopilot 读取 |
| `Sites.Read.All` | SharePoint 站点、列表读取 |
| `Files.Read.All` | SharePoint 与 OneDrive 文件读取 |
| `Team.ReadBasic.All` | Teams 基础读取 |
| `Channel.ReadBasic.All` | 频道读取 |
| `TeamMember.Read.All` | 团队成员读取 |
| `TeamsAppInstallation.Read.Group` | 团队已安装应用读取 |
| `OnlineMeetings.Read.All` | 在线会议读取 |
| `ServiceHealth.Read.All` | 服务健康与 service issue |
| `ServiceMessage.Read.All` | Message Center |
| `SecurityAlert.Read.All` | Defender 告警 |
| `SecurityIncident.Read.All` | 安全事件 |
| `SecurityEvents.Read.All` | Secure Score |
| `IdentityRiskyUser.Read.All` | 风险用户 |
| `IdentityRiskEvent.Read.All` | 风险检测 |

不要一次性授予 write 权限再依靠 enabledTools 限制。
权限应该与当前开放动作同步扩张。

### 3.3 写权限扩展

启用写动作前，按业务范围逐项添加：

| 权限 | 高风险写能力 |
|---|---|
| `User.ReadWrite.All` | 用户增删改、许可证分配 |
| `Group.ReadWrite.All` | 组创建删除更新 |
| `GroupMember.ReadWrite.All` | 组成员增删 |
| `Directory.ReadWrite.All` | 许可证分配相关目录操作 |
| `Policy.ReadWrite.ConditionalAccess` | Conditional Access 创建、修改、删除 |
| `Application.ReadWrite.All` | 应用和 service principal 创建删除 |
| `RoleManagement.ReadWrite.Directory` | 目录角色分配和移除 |
| `UserAuthenticationMethod.ReadWrite.All` | MFA 方法删除 |
| `DeviceManagementManagedDevices.ReadWrite.All` | Intune 设备属性修改 |
| `DeviceManagementManagedDevices.PrivilegedOperations.All` | wipe、retire、reboot、lock 等设备动作 |
| `DeviceManagementConfiguration.ReadWrite.All` | 合规与配置策略写入 |
| `DeviceManagementApps.ReadWrite.All` | Intune 应用、分配 |
| `DeviceManagementServiceConfig.ReadWrite.All` | 注册、Autopilot |
| `Sites.Manage.All` | SharePoint 列表创建、修改、删除 |
| `Sites.ReadWrite.All` | SharePoint 列表项写入 |
| `Sites.FullControl.All` | SharePoint 权限读取和共享链接删除 |
| `Files.ReadWrite.All` | OneDrive 与 drive item 共享 |
| `Channel.Create.Group.All` | Teams 频道创建 |
| `ChannelSettings.ReadWrite.All` | Teams 频道属性写入 |
| `Channel.Delete.All` | Teams 频道删除 |
| `TeamMember.ReadWrite.All` | 团队成员增删 |
| `OnlineMeetings.ReadWrite.All` | 在线会议创建和删除 |
| `SecurityAlert.ReadWrite.All` | 安全告警分诊 |
| `SecurityIncident.ReadWrite.All` | 安全事件分诊 |
| `IdentityRiskyUser.ReadWrite.All` | 风险用户确认与 dismiss |

`Sites.FullControl.All`、`RoleManagement.ReadWrite.Directory` 和 `DeviceManagementManagedDevices.PrivilegedOperations.All` 应按需临时授予。
不要为了减少配置步骤一次性授予所有目录角色。

## 4. PowerShell upstream 应用

### 4.1 Exchange Online 与 Security & Compliance

1. 创建应用 `m365-powershell-upstream`。
2. 上传 `secrets/powershell.crt`。
3. 记录 client ID，写入 `M365_POWERSHELL_CLIENT_ID`。
4. 为 Office 365 Exchange Online 添加 Application permission `Exchange.ManageAsApp`。
5. 为 Microsoft Exchange Online Protection 添加 Application permission `Exchange.ManageAsApp`。
6. 对两项权限执行 Grant admin consent。
7. 把该应用的服务主体加入所需 Entra 角色。

| 角色 | 适用能力 |
|---|---|
| Exchange Administrator | 邮箱、邮件流、连接器、隔离释放 |
| Security Administrator | 反垃圾、反钓鱼、Purview 安全能力 |
| Compliance Administrator | retention、DLP、合规读取 |
| Teams Administrator | Teams 策略分配与用户策略 |
| SharePoint Administrator | 租户网站集与 PnP 管理 |

生产环境优先使用 Exchange 自定义 role group 限制 cmdlet 和 Write scope。
只赋 `Exchange Administrator` 会得到较宽的 Exchange 管理面。
`Global Reader` 不足以执行写操作。
不要在无人值守 MCP 应用上分配 Global Administrator。

### 4.2 Teams Graph 权限

Microsoft Teams PowerShell 的 app-only 模式需要 Graph Application permissions 与 Teams Administrator role。
按官方模块要求授予：

```text
User.Read.All
Group.ReadWrite.All
AppCatalog.ReadWrite.All
TeamSettings.ReadWrite.All
Channel.Delete.All
ChannelSettings.ReadWrite.All
ChannelMember.ReadWrite.All
```

这些权限只属于 PowerShell upstream 应用，不要加到 Graph MCP 应用。
如果只使用 Teams 读取动作，仍建议先用隔离 tenant 验证模块对当前权限集的要求。

### 4.3 SharePoint PnP

PnP.PowerShell app-only 连接使用证书和自建 Entra 应用。
在当前 catalog 中，SharePoint tenant admin 动作使用 `Sites.FullControl.All`。
生产只授权实际需要的 tenant admin cmdlet，并分配 SharePoint Administrator role。
PnP 3.x 要求 PowerShell 7.4 或更高版本。
本项目容器使用 PowerShell 7.6。

## 5. MCP resource API 配置

### 5.1 Graph MCP resource

1. 创建应用 `m365-graph-mcp-api`。
2. 不创建 secret，不添加 Microsoft Graph permission。
3. 在 Expose an API 设置 Application ID URI 为 `api://m365-graph-mcp`。
4. 创建两个 App role。
5. 两个 role 的 Allowed member types 都设为 Applications。

| Display name | Value | 说明 |
|---|---|---|
| MCP Read | `M365.MCP.Read` | 可搜索、读取和执行只读业务动作 |
| MCP ReadWrite | `M365.MCP.ReadWrite` | 额外允许执行写动作，仍需 confirmation |

把 把 `api://m365-graph-mcp` 写入 `MCP_GRAPH_AUDIENCE`，或直接使用默认值。
如果实际 token 的 `aud` claim 是资源应用 client ID GUID 而不是 Application ID URI，应把 `MCP_GRAPH_AUDIENCE` 设置为实际 GUID。
不要关闭 audience 校验。

### 5.2 PowerShell MCP resource

重复上述步骤，创建 `m365-powershell-mcp-api`。
Application ID URI 使用 `api://m365-powershell-mcp`。
App role value 必须与 Graph MCP resource 保持一致。
把 URI 写入 `MCP_POWERSHELL_AUDIENCE`。
同样以实际 token `aud` claim 为准。

## 6. Agent 应用与角色分配

1. 创建或复用 Agent Runtime 的 confidential client。
2. 记录 client ID，写入 `MCP_ALLOWED_CLIENT_IDS`。
3. 在 API permissions 添加 `m365-graph-mcp-api` 的 Application permission。
4. 选择 `M365.MCP.Read` 或 `M365.MCP.ReadWrite`。
5. 对 `m365-powershell-mcp-api` 重复配置。
6. 执行 Grant admin consent。
7. 确认 Enterprise applications 页面中 Agent 服务主体已持有相应 app role assignment。

如果 Graph MCP 和 PowerShell MCP 需要不同 Agent、不同角色，可以使用两个 Agent 应用。
`MCP_ALLOWED_CLIENT_IDS` 支持逗号分隔多个 client ID。
所有允许的 client ID 共享同一组 MCP role 规则。
更细的 per-client 权限目前应在 Entra 中通过不同 audience 或独立部署实现。

## 7. 入口 JWT 校验参数

| 环境变量 | 值 |
|---|---|
| `MCP_ENTRY_TENANT_ID` | 签发入口 token 的 tenant ID |
| `MCP_GRAPH_AUDIENCE` | `api://m365-graph-mcp` |
| `MCP_POWERSHELL_AUDIENCE` | `api://m365-powershell-mcp` |
| `MCP_ALLOWED_CLIENT_IDS` | Agent 应用 client ID |
| `MCP_READ_ROLES` | `M365.MCP.Read,M365.MCP.ReadWrite` |
| `MCP_WRITE_ROLES` | `M365.MCP.ReadWrite` |

MCP 使用 tenant v2 issuer：

```text
https://login.microsoftonline.com/<tenant-id>/v2.0
```

默认 JWKS：

```text
https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
```

如果使用自定义 issuer 或 national cloud，显式设置 `MCP_GRAPH_ISSUER`、`MCP_GRAPH_JWKS_URI`、`MCP_POWERSHELL_ISSUER` 或 `MCP_POWERSHELL_JWKS_URI`。
当前 catalog 和 HTTP 客户端针对商业云实现，national cloud 不应只替换 issuer。

## 8. 验证 token

Agent 应用配置完成后，可在 Agent Runtime 中取得 app-only token。
解码并确认以下 claim：

```text
aud = api://m365-graph-mcp 或 api://m365-powershell-mcp
tid = 配置的 tenant
roles 包含 M365.MCP.Read 或 M365.MCP.ReadWrite
azp 或 appid = Agent client ID
idtyp = app
```

App-only token 通常没有 `scp` claim。
不要用 `scp` 判断本项目的 MCP 写权限。
不要关闭 audience 或 tenant 校验来让错误 token “先跑起来”。

## 9. 权限变更流程

1. 在隔离 tenant 启用目标动作。
2. 确认缺失的 Graph Application permission 或 Entra role。
3. 更新应用权限并完成 admin consent。
4. 用只读调用验证同一身份能访问目标资源。
5. 用隔离测试对象完成写操作和回滚。
6. 记录测试证据到 review registry 的 `evidence`。
7. 才把动作加入生产 enabledTools。

不要直接从 Microsoft Graph Explorer 的 delegated 成功结果推断 app-only 权限足够。
两者使用不同 permission 类型和不同 token claim。
