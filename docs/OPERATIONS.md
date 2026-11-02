# 运维手册

## 1. 日常健康检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8001/readyz
docker compose logs --since=15m graph-mcp powershell-mcp
```

关注：

| 信号 | 含义 |
|---|---|
| `writeConfirmation=false` | 公钥缺失或不可读，写操作会失败关闭 |
| `/readyz` 503 | Catalog 或 enabledTools 配置错误 |
| 容器 restart | 启动配置、证书、内存或 OOM |
| 403 增加 | 入口 app role、audience 或 client ID 异常 |
| 429 增加 | Microsoft Graph 节流 |
| confirmation 错误增加 | 审批 token 错误、过期或重放尝试 |
| `semanticReady=false` | embedding endpoint、模型或 action index 不可用 |
| `searchMode=lexical_fallback` | semantic 不可用但 fallback 已启用 |
| embedding p95 延迟上升 | DGX Spark 与 LLM 资源竞争或网络异常 |
| `powershell_timeout` 增加 | cmdlet 查询过宽或模块连接问题 |

## 2. 部署切换

1. 先生成新的 Inventory diff。
2. 对比新增、删除、风险变更和权限变更。
3. 确认新增动作仍为 pending。
4. 检查 `.env` 是否有新增必填项。
5. 检查 secret 是否仍存在。
6. 构建新镜像。
7. 一次只升级一个 Server。
8. 调 `/readyz`。
9. 跑 smoke。
10. 再升级另一个 Server。

不要在升级窗口同时扩大 enabledTools 和 upstream 权限。
先验证软件，再验证权限。

## 3. 证书轮换

Entra 应用允许同一应用同时存在多个有效证书。
建议采用重叠轮换：

1. 生成新证书，保留旧证书。
2. 上传新 `.crt` 到 Entra 应用。
3. 等 Entra 证书生效。
4. 把新 PFX 和 password 放入 Docker secret。
5. 滚动重启对应 MCP。
6. 用只读动作验证 token acquisition 和上游连接。
7. 停用旧证书。
8. 重新生成 smoke token。

不要先删除旧证书再重启。
不要在同一个时间窗口同时轮换 Graph 和 PowerShell upstream，除非已经演练。

## 4. 审批密钥轮换

当前实现只支持一个 verification public key。
轮换需要协调：

1. 停止审批系统签发旧 token。
2. 等待已签发 token 的 `exp` 过期。
3. 生成新的 RSA keypair。
4. 把新 public key 放到 Docker secret。
5. 重启两个 MCP。
6. 把新 private key 放入审批系统。
7. 恢复签发。
8. 验证写操作。

在没有多 key 支持前，不要直接热替换 public key 并期望旧 token 继续可用。
建议审批 TTL 不超过 5 分钟，轮换窗口因此较短。

## 5. 上游权限漂移

Microsoft 365 权限可能因为以下原因漂移：

1. 管理员在 Entra 中移除了 Application permission。
2. 管理员撤销了 app role assignment。
3. Exchange role group 发生变更。
4. SharePoint admin role 变更。
5. Conditional Access 阻止了服务身份。
6. Teams app-only 策略变化。

检测方法：

1. 用 smoke 获取新 token。
2. 执行一个只读业务动作。
3. 检查 MCP 返回的 Graph error code 或 PowerShell error。
4. 检查 Entra sign-in/audit、Exchange admin audit、Purview audit。
5. 与 Catalog 的 required_permissions 比对。

不要让 Agent 通过反复重试猜权限。
权限缺失应返回可操作的错误，并由平台管理员修复。

## 6. 写操作失败对账

写操作可能处于三种状态：

| 状态 | 含义 | 处理 |
|---|---|---|
| 明确失败 | MCP 未调用 upstream 或 upstream 明确返回错误 | 修复后重新审批 |
| 明确成功 | upstream 返回 2xx | 记录结果 |
| 不确定 | MCP 与 upstream 之间断连、超时、进程终止 | 用 M365 侧数据对账 |

常见对账方法：

1. 用只读动作检查目标对象当前状态。
2. 用 Microsoft 365 audit log 检查对应操作和 app identity。
3. 用 exchange message trace 检查邮件流。
4. 用 Intune device action status 检查设备动作。
5. 用 SharePoint permission list 检查共享变化。
6. 用 Entra audit log 检查角色、用户、组变化。

对账完成前不要重新提交同一写审批。
重新审批时应该在 reason 中写明已核对和原 correlation ID。

## 7. Graph 节流

Graph 返回 429 时：

1. Graph GET 在安全范围内进行有限重试。
2. 写操作不自动重试。
3. Agent 应减少 page size、加 filter、缩小 select。
4. 大量盘点和报表应放到专门任务窗口。
5. 不要通过增加副本绕过 tenant throttle。

Service Communications API 有独立的 per-tenant per-app throttle。
Message Center 和 service health 查询可能与其他 Graph 查询共享 tenant 预算。

## 8. PowerShell 性能

PowerShell 每次请求启动独立进程并重新连接模块。
这是安全隔离的代价。
优化顺序：

1. 缩小 identity、date range、result size。
2. 避免一次查询全 tenant。
3. 降低 `M365_PS_MAX_CONCURRENCY`，避免 CPU 和内存争抢。
4. 把报表类任务放到独立实例或离线任务。
5. 不要为了性能改成常驻任意脚本会话。

如果某个高频动作无法接受启动开销，优先确认 Graph 是否有等价稳定 endpoint。
不要在 MCP 中缓存邮箱内容或大批量 PowerShell 对象。

## 9. 配置热加载

Review registry 和 enabledTools 都按 mtime 热加载。
修改后：

```bash
uv run m365-inventory validate
curl -fsS http://127.0.0.1:8000/readyz
```

不要用编辑器生成临时文件后留下半写状态。
CLI 使用临时文件和 atomic rename。
手工编辑时应先写临时文件，再 `mv` 替换。

## 9.1 Hybrid index 重建

Action vectors 在 MCP startup 时生成，不是持久化索引。
以下变化必须重启 MCP 并重建：

1. Embedding model id 变化。
2. Model revision 或 instruction 变化。
3. Vector dimension 变化。
4. MCP 镜像中的 Catalog action 变化。
5. Action description、keywords 或 domain 变化。

模型变化后不能复用旧向量。
即使 dimension 相同，不同模型或不同 instruction 的向量也不能混用。
重建后检查 `/readyz` 的 `semanticReady`、`semanticActions` 和 `semanticDimension`。

如果 embedding 服务重启但 MCP 保持运行，MCP 不会自动重建 action vectors。
只有 startup 阶段初始化失败时，才需要修复服务并重启 MCP。

## 10. 日志与指标

最小告警建议：

| 告警 | 条件 |
|---|---|
| Server down | `/readyz` 连续失败 2 分钟 |
| Valkey down | `valkey-cli ping` 失败 |
| Confirmation failure spike | 5 分钟内多次 `confirmation_*` 错误 |
| Write failure spike | 写动作错误率超过阈值 |
| 403 spike | 入口或 upstream 权限错误 |
| PowerShell timeout | 5 分钟内多次 timeout |
| Certificate expiry | 30 天内证书到期 |
| Confirmation key expiry | 30 天内审批密钥到期 |

日志字段：

```text
event
server
action
operation
risk
actor
correlation_id
outcome
duration_ms
error_code
approver
reason
```

不要把完整 HTTP response、邮件正文、文件内容或审批 token 写入日志。

## 11. 备份

需要备份：

1. `config/review-registry.json`
2. `config/enabledTools.json`
3. `secrets/confirmation_public.pem` 的受控副本
4. Inventory 导出
5. Compose 和 `.env` 的非 secret 模板

不需要备份：

1. `.env` 中的 secret 明文
2. Graph upstream PFX private key
3. confirmation private key
4. 业务响应

Valkey 的 `valkey-data` 是运行时安全状态。
备份可以保留，但恢复旧数据可能重新打开已消费 jti 的重放风险。
恢复 Valkey 时应同时撤销仍在有效期内的审批 token。

## 12. 关闭与下线

1. 从 enabledTools 移除所有写动作。
2. 停止审批系统签发新 token。
3. 等待未过期 token 失效。
4. 停止 MCP 容器。
5. 撤销 upstream 证书。
6. 撤销 Agent app role。
7. 删除 Agent client ID allow-list。
8. 归档审计、Review 和 Inventory。
9. 删除 secrets 和 `.env`。
10. 如需删除 Valkey 卷，确认没有未完成的对账。
