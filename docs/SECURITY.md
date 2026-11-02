# 安全模型与威胁边界

## 1. 安全目标

1. Agent 只能在 Catalog 声明的固定动作中选择，不能构造任意管理请求。
2. 未完成 Review 的动作不能执行。
3. 写操作必须经过外部审批并绑定精确参数。
4. MCP 被攻破时不能伪造审批，审批系统被攻破时不能直接获得 M365 应用证书。
5. 入口 token 不能转发给 Microsoft 365。
6. 日志不能被用于还原 Bearer token、证书 password 或审批 token。
7. 结果不能因为无限分页或无限输出拖垮容器。

## 2. 信任边界

| 边界 | 一侧 | 另一侧 | 主控制 |
|---|---|---|---|
| B1 | Agent Runtime | MCP ingress | Entra JWT、app role、audience、client allow-list |
| B2 | MCP | Microsoft Graph | 独立证书、Graph Application permission |
| B3 | MCP | Exchange / Compliance | 独立证书、Exchange.ManageAsApp、Entra role |
| B4 | MCP | Teams / SharePoint admin | 独立证书、Graph 权限、Entra role |
| B5 | MCP | Change / Approval System | RS256 confirmation、params digest、actor、jti |
| B6 | MCP | Valkey | 私有 Docker network、无认证仅限网络隔离 |
| B7 | 容器 | Linux Host | non-root、read-only rootfs、cap_drop、secrets |
| B8 | MCP | DGX Spark embedding service | 私有网络、内部认证、无原始 query 日志 |

任何一条边界失效都不应自动扩大到另一条边界。

## 3. 主要威胁与控制

### 3.1 Prompt injection 导致危险写操作

威胁：Agent 读取邮件、网站或文档中的恶意指令，试图执行删除或授权。

控制：

1. Tool Catalog 只暴露固定动作，不允许任意 endpoint 或脚本。
2. 动作有独立 risk level。
3. 写操作需要独立审批系统签发 token。
4. approval token 绑定 action、params、actor，不能“批准一个动作后改参数”。
5. MCP 不根据业务文本自动批准。
6. 工具描述不包含要求 Agent 跳过授权或调用外部工具的指令。

残余风险：拥有审批权限的人可能被社工。
审批系统必须展示规范化后的 action 和参数，不要只展示自然语言摘要。

### 3.2 入口 token 伪造或 audience 混用

威胁：攻击者用其他 API 的 token 调 MCP，或用 Graph MCP token 调 PowerShell MCP。

控制：

1. JWT 签名通过 JWKS 校验。
2. issuer 和 audience 必须精确匹配。
3. tenant 必须匹配。
4. app role 必须存在。
5. client ID 必须允许。
6. 两个 MCP 使用不同 audience。

残余风险：如果 JWKS endpoint 被 DNS 或 TLS 攻击，验证可能受影响。
Host 应使用企业 DNS 和可信 CA，不关闭 TLS 校验。

### 3.3 Token passthrough

威胁：MCP 把入口 token 直接转发给 Graph，导致权限混淆。

控制：

1. 入口 token 与上游 token 完全分离。
2. Graph 使用独立证书 client credential。
3. PowerShell 使用独立证书 client credential。
4. 入口 token 不传给 `GraphClient`，也不传给 PowerShell worker。

### 3.4 重放审批 token

威胁：合法审批被重复使用，造成重复删除、重复授权。

控制：

1. confirmation 有短 `exp`。
2. confirmation 绑定 `jti`。
3. MCP 在上游调用前用 Valkey `SET NX` 占用 jti。
4. 并发请求只能有一个成功。
5. Valkey 使用 AOF 持久化。
6. `valkey-data` 删除会使未过期 jti 丢失，运维必须同时撤销审批。
7. 写操作不自动重试。

残余风险：Valkey 完全丢失且审批 token 仍在有效期内，存在重放窗口。
生产应设置 5 分钟以内 TTL，并监控 Valkey 健康。

### 3.5 改动审批参数

威胁：审批一个安全参数，执行时替换为危险目标。

控制：

1. 审批 token 绑定 canonical `params_sha256`。
2. MCP 在执行前重新校验 Schema 并重新计算 digest。
3. action 必须一致。
4. actor 必须一致。
5. 审批系统保存完整 canonical 参数和 digest。

残余风险：审批 UI 与签名服务对参数的解释不一致。
两者必须使用同一 canonicalization 规则。

### 3.6 Command injection

威胁：在 PowerShell 参数中注入 `; Remove-Mailbox` 或表达式。

控制：

1. 不允许任意 cmdlet。
2. 参数通过 JSON stdin 传入。
3. PowerShell 使用 hashtable splatting，不拼接命令字符串。
4. 没有 `Invoke-Expression`。
5. Python 子进程不经过 shell。
6. worker 再次核对 action 和 cmdlet。

残余风险：某些 PowerShell 参数本身接受脚本块或表达式。
Catalog 必须避免这种参数，Review 时必须拒绝自由表达式字段。

### 3.7 SSRF

威胁：调用方通过 Graph path 或 nextLink 访问内部服务。

控制：

1. Graph path 来自 Catalog，不是调用方输入。
2. path 占位符只接受标量并 URL encode。
3. nextLink 必须与配置的 Graph origin 和 `/v1.0` path 同源。
4. Graph base URL 只允许 Microsoft Graph commercial cloud。
5. 不提供通用 HTTP proxy 工具。

### 3.8 Embedding 服务成为数据外流或注入边界

威胁：MCP 把完整 query 发送给不可信的外部模型服务，或模型返回恶意向量影响排序。

控制：

1. Embedding endpoint 由配置固定，不接受 Agent 提供 URL。
2. 默认只允许私有 DGX Spark endpoint，不能访问公网 embedding API。
3. MCP 发送的只有 search intent，不发送 M365 token、密码或业务响应。
4. Embedding 服务不参与 enabled、Review 或 confirmation。
5. 响应必须校验数量、索引、dimension、有限值和零向量。
6. 模型 revision 和维度通过配置校验。
7. Embedding 服务日志不记录原始 query body。
8. MCP 只记录 query hash、结果数、延迟和错误码。
9. Hybrid 失败时显式返回 fallback 状态，不静默继续。
10. 模型和 TEI 镜像必须 pin revision/digest。

残余风险：search intent 可能包含用户名、邮箱或站点名。
虽然是私有网络，仍需把 embedding 服务视为敏感数据处理组件。
如果使用外部 embedding provider，必须做 DLP、合同和网络评审。

### 3.9 结果或分页导致 DoS

威胁：读取全 tenant 或巨大 CSV，耗尽内存。

控制：

1. `limit` 有最大值。
2. `select` 和 `filter` 有长度限制。
3. Graph 默认最多 5 页。
4. 响应序列化后最大 1 MB。
5. PowerShell 并发默认 4。
6. PowerShell 每次执行有 timeout。
7. 超时杀进程组。
8. 容器建议设置 CPU、内存和 pids limit。

### 3.10 证书和密钥泄漏

威胁：镜像、日志、环境变量或 Git 泄漏私钥。

控制：

1. `.gitignore` 排除 `.env` 和 `secrets/`。
2. `.dockerignore` 不打包 secrets。
3. Docker secret 只读挂载。
4. 不把 certificate password 作为命令行参数。
5. 日志不打印环境变量。
6. confirmation 私钥不放在 MCP Host。
7. 容器非 root，drop capabilities。

残余风险：Host 被 root 攻破时 Docker secret 可被读取。
生产 Host 应限制 SSH、启用磁盘加密和审计。

### 3.11 Review 配置被篡改

威胁：攻击者直接改 `review-registry.json` 和 `enabledTools.json`。

控制：

1. 配置文件权限限定给平台管理员。
2. 配置变更走 GitOps 或变更管理系统。
3. 审计配置文件的文件系统事件。
4. 写操作仍需要 confirmation，单靠 enabledTools 不能执行。
5. 定期比对 Inventory、Review 和 enabledTools。

残余风险：拥有 Host 文件写权限的人可以批准动作。
这是平台管理员权限，应纳入高权限账号治理。

### 3.12 Catalog 供应链

威胁：新增动作的说明或定义中嵌入 prompt injection。

控制：

1. Tool description 由代码 Review。
2. description 不允许行为指令或外部拉取指令。
3. Catalog 只从镜像读取，运行时不下载远程 Catalog。
4. 权限和风险评估纳入 PR。
5. 生成脚本和测试在 CI 校验。

### 3.13 日志泄漏

威胁：审计日志包含邮件正文、token 或参数中的个人数据。

控制：

1. 审计只记录 action、actor、risk、correlation ID、结果和耗时。
2. `reason` 截断到 500 字符，且由调用方明确提交。
3. 异常信息不包含上游 Authorization header。
4. Graph 错误只保留 code、message 摘要和 request ID。
5. PowerShell stderr 只返回截断诊断，不写入审计正文。

残余风险：`reason` 可能包含敏感信息。
审批和 Agent 规则应禁止在 reason 写入个人数据或凭据。

## 4. App-only 身份的责任边界

所有 MCP 上游操作使用服务身份。
Microsoft 365 审计日志中记录的是 MCP 应用，不一定记录实际发起请求的 Agent。
MCP 自身审计日志把入口 `actor` 与 action 关联起来。
企业需要把两类日志通过时间、correlation ID 和 change record 对齐。
如果业务要求每个终端用户身份都必须出现在 M365 审计中，需要改为 delegated OAuth 和 on-behalf-of 设计，这不是当前实现。

## 5. 最小权限建议

1. Graph upstream 只授当前 enabled 动作需要的 Application permission。
2. PowerShell upstream 只授当前 enabled 服务需要的 Entra role。
3. 不使用 Global Administrator。
4. 把高风险写动作放在独立部署和独立审批范围。
5. 如果不同业务域需要不同权限，部署多套 MCP 实例，不要用一个 super app 覆盖全部。
6. Agent 的 MCP app role 按读写阶段分配。
7. 测试 Agent 只给 read role。
8. 生产写 Agent 使用单独 client ID 和更强审批。

## 6. 安全运维

| 频率 | 操作 |
|---|---|
| 每次发布 | 跑 lint、test、wheel build、Catalog validate |
| 每次发布 | 核对 enabledTools 与 Review diff |
| 每日 | 检查 `/readyz`、容器重启、Valkey 健康 |
| 每周 | 检查异常写入审批、失败 403、重复 confirmation |
| 每月 | 审计 upstream app 权限和 Entra role |
| 每季度 | 轮换证书和审批密钥协调测试 |
| 变更后 | 导出 Inventory，归档 Review evidence |

## 7. 事故响应

如果怀疑 Agent 被入侵：

1. 从 `MCP_ALLOWED_CLIENT_IDS` 移除 Agent client ID。
2. 禁用对应 enabledTools 中的写动作。
3. 撤销 Agent 的 app role assignment。
4. 保留审计日志和审批记录。
5. 检查最近 `execute_write` 的 actor、approver 和 correlation ID。

如果怀疑 MCP 容器被入侵：

1. 停止 graph-mcp 和 powershell-mcp。
2. 撤销 upstream 证书。
3. 轮换 confirmation public key 和审批私钥。
4. 轮换 Valkey 访问控制或重建网络。
5. 审计 Docker Host、secrets 和镜像来源。

如果怀疑 approval token 泄漏：

1. 立即把 `exp` 窗口作为影响范围。
2. 检查 jti 是否已被占用。
3. 轮换审批签名密钥。
4. 审核对应 action 的最近执行。
5. 如果写操作未自动重试，按 correlation ID 和 change record 对账。

如果怀疑 embedding 服务泄漏或被污染：

1. 把 `MCP_SEARCH_MODE` 切换为 `lexical` 并重启两个 MCP。
2. 轮换 embedding API key 或内部认证凭据。
3. 检查 embedding 服务是否记录了原始 query。
4. 恢复固定模型 revision，重建全部 action vectors。
5. 检查 hybrid 阈值和搜索结果是否异常。
6. 如果 embedding 服务曾接触敏感 query，按数据事件流程处理。

## 8. 已知安全边界

1. 当前实现没有多 tenant 路由。
   一个部署对应一个 upstream tenant 和一个入口 tenant。
2. 当前 confirmation 只支持一个验证公钥，不支持同时挂载多个 `kid`。
3. 当前没有内置 SIEM 推送，仅输出 JSON stdout。
4. 当前没有 per-action rate limit，只有容器级并发和 Graph 节流。
5. 当前没有 OAuth protected resource metadata 或交互式客户端登录。
6. 当前没有用户级 delegated 权限模型。
7. 当前没有 Docker image 签名和 SBOM 生成流程。
8. 当前没有对 Microsoft API 的实时 egress allow-list 实现。
9. Hybrid search 依赖 DGX Spark embedding 服务，服务不可用会影响动作发现。
10. Hybrid 阈值、权重和模型尚未用真实 tenant 查询集校准。
11. 模型或 revision 变化后需要重启 MCP 并重建 action vectors，没有自动 reindex。
12. Query intent 可能含用户、邮箱或站点名，embedding 服务必须按敏感数据处理。
13. 当前没有 embedding 服务客户端证书或 mTLS 内置实现，认证依赖反向代理或网络策略。

这些边界在进入高敏感生产环境前应由平台安全团队确认。
