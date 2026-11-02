# 部署与运行

## 1. 前置条件

| 依赖 | 版本或说明 |
|---|---|
| Linux Host | 支持 Docker Engine 与 Compose v2 |
| Docker Engine | 建议当前稳定版 |
| Docker Compose | 支持 `depends_on.condition` 与 secrets 的 v2 版本 |
| OpenSSL | 生成证书和审批密钥 |
| `uv` | 仅 Host 本地测试、Review CLI 和 token 脚本需要 |
| Microsoft 365 | 商业云 tenant 与管理员权限 |
| Entra 应用 | 按 `ENTRA-SETUP.md` 完成配置 |
| DGX Spark embedding | OpenAI-compatible `/v1/embeddings`，TEI 121 或等价服务 |

本文以项目目录 `/opt/m365-mcp` 为例，可替换为实际路径。
所有命令默认在项目根目录执行。

## 2. 部署文件

| 文件 | 用途 |
|---|---|
| `docker-compose.yml` | graph-mcp、powershell-mcp、Valkey 与 smoke Agent |
| `deploy/Dockerfile.graph` | Python Graph Server 镜像，含独立 smoke Agent target |
| `deploy/Dockerfile.powershell` | PowerShell 7.6 与三类模块镜像 |
| `.env.example` | 非 secret 配置模板 |
| `config/enabledTools.json` | 每个 Server 的动作 allow-list |
| `config/review-registry.json` | 人工 Review 结果 |
| `secrets/` | 证书、password 与审批公钥，禁止提交 |
| `inventory/` | 生成的 Tool Inventory 报表 |

两个业务容器都使用非 root UID 10001。
业务容器使用只读根文件系统、`cap_drop: ALL` 和 `no-new-privileges`。
配置目录以只读卷挂载，容器只能读取并热加载 Review 与 enabledTools。
Review CLI 在 Host 上修改配置，容器不需要写权限。
Valkey 使用 AOF 持久化已消费 jti。

## 3. 准备配置

### 3.1 创建 .env

```bash
cp .env.example .env
chmod 600 .env
```

至少填写：

```text
M365_TENANT_ID
M365_ORGANIZATION
M365_SHAREPOINT_ADMIN_URL
M365_GRAPH_CLIENT_ID
M365_POWERSHELL_CLIENT_ID
MCP_ENTRY_TENANT_ID
MCP_GRAPH_AUDIENCE
MCP_POWERSHELL_AUDIENCE
MCP_ALLOWED_CLIENT_IDS
MCP_CONFIRMATION_ISSUER
MCP_CONFIRMATION_MAX_TTL_SECONDS
MCP_SEARCH_MODE
MCP_SEARCH_EMBEDDING_URL
MCP_SEARCH_EMBEDDING_MODEL
MCP_SEARCH_EMBEDDING_DIMENSION
```

`M365_ORGANIZATION` 必须是主 `.onmicrosoft.com` 域名。
`M365_SHAREPOINT_ADMIN_URL` 是 SharePoint admin center URL，例如 `https://contoso-admin.sharepoint.com`。
`MCP_ENTRY_TENANT_ID` 通常与 `M365_TENANT_ID` 相同。
`MCP_ALLOWED_CLIENT_IDS` 是允许调用 MCP 的 Agent 应用 client ID。
`MCP_GRAPH_AUDIENCE` 和 `MCP_POWERSHELL_AUDIENCE` 必须与 Entra resource API 的 Application ID URI 完全一致。

### 3.2 放置 secrets

```bash
./scripts/create-service-certificate.sh graph
./scripts/create-service-certificate.sh powershell
./scripts/create-service-certificate.sh agent
./scripts/create-confirmation-keypair.sh
```

最终目录结构：

```text
secrets/
  graph.crt
  graph.key
  graph.pfx
  graph.pfx.password
  powershell.crt
  powershell.key
  powershell.pfx
  powershell.pfx.password
  agent.crt
  agent.key
  agent.pfx
  agent.pfx.password
  confirmation_signing.pem
  confirmation_public.pem
```

`.crt` 上传到 Entra。
`.key` 和 `.pfx.password` 应转入企业 secret manager。
`confirmation_signing.pem` 应转移到审批系统，不要留在 MCP Host。
MCP Host 只需要 `.pfx`、password 文件和 `confirmation_public.pem`。
从 MCP Host 移除 `confirmation_signing.pem` 可以降低被入侵后伪造审批的风险。

### 3.3 检查配置目录权限

```bash
chmod 755 config
chmod 644 config/enabledTools.json config/review-registry.json
chmod 700 secrets
chmod 600 secrets/*
```

容器以 UID 10001 读取配置。
配置目录只需可读，容器不自动写 Review。
如果 Host 的 ACL 阻止 UID 10001 读取，使用 ACL 或调整 group，不要使用 `chmod 777`。
管理员在 Host 上运行 Review CLI，不需要容器用户写配置。

## 4. 初始 enabledTools

仓库当前基线在 `config/enabledTools.json` 中启用了 159 个 implemented read action：
Graph 100 个，PowerShell 59 个。
127 个 write action 和 12 个未实现 action 仍为 disabled。
这批只读启用使用 owner 批量批准，evidence 为 `OWNER-APPROVED: bulk read-only enablement for Agent onboarding (2026-11-02)`。
进入生产前应替换为逐 domain 的正式 Review 证据，并先完成 tenant 权限验收。
不要把初始状态扩展成“全部启用”。

## 5. 构建镜像

```bash
docker compose build graph-mcp powershell-mcp
```

Graph 镜像基于 Python 3.13 slim。
PowerShell 镜像基于 Ubuntu 24.04，并从官方 GitHub release 安装校验过 SHA-256 的 PowerShell 7.6.9。
当前 MCR 还没有发布 7.6 Ubuntu 镜像，因此不能使用 `mcr.microsoft.com/powershell:7.6-ubuntu-24.04`。
镜像内安装：

```text
ExchangeOnlineManagement 3.10.1
MicrosoftTeams 8.0.0
PnP.PowerShell 3.4.1
```

ExchangeOnlineManagement 3.10.x 要求 PowerShell 7.6。
如果把镜像改回 7.5，Exchange 与 Compliance 连接会失败。
构建阶段需要访问 GitHub release、PowerShell Gallery 和 apt repository。
运行阶段不需要访问 PowerShell Gallery。
PowerShell tarball 的版本和 SHA-256 固定在 `deploy/Dockerfile.powershell`。

## 6. 启动

```bash
docker compose up -d valkey graph-mcp powershell-mcp
docker compose ps
```

预期三个容器为 healthy。
`graph-mcp` 和 `powershell-mcp` 的端口默认只绑定 Host 的 `127.0.0.1`。
这避免 MCP 未经过网关直接暴露到外部网络。

查看启动日志：

```bash
docker compose logs -f graph-mcp powershell-mcp
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8001/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8001/readyz
```

`healthz` 只表示进程存活。
`readyz` 检查 Catalog、enabledTools、confirmation 和 hybrid search 状态。
`ready` 为 true 不代表每个业务动作的 Microsoft 权限都已验证。

### 6.1 Hybrid search 配置

MCP 镜像不包含 embedding 模型。
Embedding 服务部署在 DGX Spark，MCP 通过 OpenAI-compatible `/v1/embeddings` 访问。

`.env` 示例：

```text
MCP_SEARCH_MODE=hybrid
MCP_SEARCH_EMBEDDING_URL=http://dgx-spark:8081/v1/embeddings
MCP_SEARCH_EMBEDDING_HEALTH_URL=http://dgx-spark:8081/health
MCP_SEARCH_EMBEDDING_API_KEY=<internal-token>
MCP_SEARCH_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
MCP_SEARCH_EMBEDDING_DIMENSION=1024
MCP_SEARCH_QUERY_INSTRUCTION=Given a Microsoft 365 administration intent, retrieve the most relevant action.
MCP_SEARCH_SEMANTIC_WEIGHT=0.55
MCP_SEARCH_LEXICAL_WEIGHT=0.45
MCP_SEARCH_SEMANTIC_FLOOR=0.30
MCP_SEARCH_SEMANTIC_CEILING=0.95
MCP_SEARCH_MIN_SCORE=0.05
MCP_SEARCH_FALLBACK_TO_LEXICAL=false
```

`dgx-spark` 必须能被两个 MCP 容器解析。
如果 DGX Spark 是独立主机，使用私有 IP 或内部 DNS。
如果 DGX Spark 与 Docker Host 是同一台机器，使用 Docker network 的 service name。

启动时每个 MCP 会批量 embedding 自己的 Catalog：

```text
graph-mcp: 181 个 action vectors
powershell-mcp: 117 个 action vectors
```

初始化成功后日志出现 `semantic_index_ready`。
`readyz` 的 `semanticReady=true` 和 `semanticActions=181` 或 `117` 表示索引可用。
如果配置了 `MCP_SEARCH_FALLBACK_TO_LEXICAL=false`，startup embedding 失败时 `/readyz` 返回 503。
如果配置为 true，`/readyz` 返回 200 和 `status=degraded`，`searchMode=lexical_fallback`。

`MCP_SEARCH_QUERY_INSTRUCTION` 和 prefix 是模型相关的。
Qwen3-Embedding 可使用当前的 instruction 形式。
如果换用 multilingual-e5，应设置 `MCP_SEARCH_QUERY_PREFIX=query: ` 和 `MCP_SEARCH_PASSAGE_PREFIX=passage: `，并清空 query instruction。
当前 `semantic_floor`、`semantic_ceiling` 和权重只是初始值。
必须使用真实运维问句的 gold set 校准后才能作为生产阈值。

### 6.2 DGX Spark TEI 示例

TEI 已提供 Blackwell 12.1 / DGX Spark GB10 的多架构镜像，但官方标记为 experimental。
镜像 tag 和 digest 必须固定。

```bash
docker run -d \
  --name m365-embedding \
  --gpus all \
  --restart unless-stopped \
  -p 8081:80 \
  -v /models:/data \
  ghcr.io/huggingface/text-embeddings-inference:121-1.9 \
  --model-id Qwen/Qwen3-Embedding-0.6B \
  --dtype float16
```

不要把 8081 暴露到公网。
MCP 到 embedding 的连接应经过私有网络、mTLS 或 API gateway。
TEI 自身没有完整的企业认证时，不要直接暴露到办公网。

DGX Spark 是 128GB unified memory，不是独立 128GB VRAM。
Chat LLM、KV cache、embedding model、OS 和 page cache 共享同一个内存池。
给 LLM 设置保守的 `gpu-memory-utilization`，并给系统预留至少 20GB。
Embedding 服务应常驻，不要每次搜索冷启动模型。
固定启动顺序，避免 LLM 和 embedding 在启动时争抢同一个内存池。
TEI 121 镜像处于 experimental 状态，必须 pin digest 并在目标 GB10 上实测。
4TB 存储足够放置多个模型，但应保留至少 20% 空闲空间给模型 cache、Docker layer 和升级。
Embedding 与 chat LLM 应使用两个独立进程或容器，不能复用 chat 模型进程。

### 6.3 Hybrid 回滚

把 `.env` 改为：

```text
MCP_SEARCH_MODE=lexical
```

然后重启两个 MCP 容器。
不需要回滚 Catalog、Review 或已执行的 action。
Hybrid 只影响动作发现，不影响 execute_read、execute_write、confirmation 或 upstream 权限。

## 7. Review 与启用新增 action

当前基线已经启用 159 个只读 action。
下面流程适用于新增 action 或替换批量批准证据：

```bash
uv run m365-inventory review \
  --server graph \
  --action entra_list_users \
  --status approved \
  --reviewer security-reviewer@example.com \
  --evidence CHG-2026-0001
```

然后启用：

```bash
uv run m365-inventory enable --server graph --action entra_list_users
```

验证配置：

```bash
uv run m365-inventory validate
uv run m365-inventory stats
```

两个 JSON 文件使用 mtime 热加载。
下一次 MCP 调用会读取新状态，无需重启容器。
如果 enabledTools 引用了不存在、未实现或未 approved 的动作，Server 会失败关闭并返回配置错误。

## 8. 获取 Agent token 并运行 smoke

`scripts/get-agent-token.py` 使用 Agent 应用证书获取 app-only token。
它只用于本地验证，不进入 MCP 镜像。

```bash
export SMOKE_GRAPH_ACCESS_TOKEN="$(
  uv run python scripts/get-agent-token.py \
    --tenant-id "$M365_TENANT_ID" \
    --client-id "$SMOKE_CLIENT_ID" \
    --certificate secrets/agent.pfx \
    --certificate-password-file secrets/agent.pfx.password \
    --scope "api://m365-graph-mcp/.default"
)"

export SMOKE_POWERSHELL_ACCESS_TOKEN="$(
  uv run python scripts/get-agent-token.py \
    --tenant-id "$M365_TENANT_ID" \
    --client-id "$SMOKE_CLIENT_ID" \
    --certificate secrets/agent.pfx \
    --certificate-password-file secrets/agent.pfx.password \
    --scope "api://m365-powershell-mcp/.default"
)"
```

两个 MCP API 的 audience 不同，必须分别获取 token。
不能拿 Graph MCP token 调 PowerShell MCP。
Agent 应用必须同时持有两个 resource API 的 app role assignment。

运行独立 smoke 容器：

```bash
docker compose --profile smoke run --rm \
  -e MCP_GRAPH_BEARER_TOKEN="$SMOKE_GRAPH_ACCESS_TOKEN" \
  -e MCP_POWERSHELL_BEARER_TOKEN="$SMOKE_POWERSHELL_ACCESS_TOKEN" \
  agent-smoke
```

smoke 容器会：

1. 调用两个 Server 的 `/healthz`。
2. 通过 Streamable HTTP 建立 MCP 连接。
3. 验证五个治理工具存在。
4. 调用 `get_tool_inventory`。
5. 输出结构化摘要并退出。

smoke 不执行任何业务写操作。
需要验证只读业务动作时，由 Agent Runtime 调用 `execute_read`，并使用隔离测试对象。

## 9. Agent Runtime 接入

Agent Runtime 是独立容器，不是本 Compose 的业务服务。
MCP endpoint：

```text
http://graph-mcp:8000/mcp
http://powershell-mcp:8001/mcp
```

Agent 容器需要加入本 Compose 的 `m365-mcp` network，或由反向代理提供等价连通性。
请求必须包含：

```http
Authorization: Bearer <app-only-entra-jwt>
Content-Type: application/json
Accept: application/json, text/event-stream
```

MCP transport 为 stateless Streamable HTTP。
Agent 不需要维护 MCP session cookie。
Agent 必须自行获取和缓存 Entra token。
MCP Server 不实现交互式 OAuth 登录和 token exchange。

如果 Agent 与 MCP 不在同一 Host，建议通过 TLS reverse proxy 暴露 endpoint。
代理必须保留 `Authorization` 和 `Host`，并把 Host 名加入 `GRAPH_MCP_ALLOWED_HOSTS` 或 `POWERSHELL_MCP_ALLOWED_HOSTS`。
服务端最终读取的变量名仍是 `MCP_ALLOWED_HOSTS`，Compose 会按 Server 注入不同值。
跨域浏览器调用还需要显式设置 `MCP_ALLOWED_ORIGINS`。
服务端到服务端 Agent 不需要浏览器 CORS。

## 10. 只读生产验收顺序

1. 在隔离 tenant 或测试对象上批准 service health 和服务健康读取。
2. 验证入口 JWT、audience、app role 和 client allow-list。
3. 批准 Entra user 和 group 读取。
4. 验证 app-only Graph permission 和 tenant 数据范围。
5. 批准 Intune 设备读取。
6. 批准 Exchange mailbox 和 message trace 读取。
7. 验证 EXO certificate app-only 与 RBAC。
8. 批准 SharePoint tenant 与 site 读取。
9. 验证 PnP certificate app-only 和 SharePoint admin role。
10. 批准 Teams 读取与策略读取。
11. 验证 Teams app-only 所需 Graph permission 和 Teams Administrator role。
12. 生成 inventory 和权限差距报告。
13. 才进入写动作评审。

每个动作的 `required_permissions` 可以在 inventory JSON、CSV 和 `describe_action` 中查到。
不要把“Schema 可执行”误认为“tenant 已授权”。

## 11. 生产加固建议

### 11.1 容器

为两个 MCP 服务增加资源限制：

```yaml
deploy:
  resources:
    limits:
      cpus: "2"
      memory: 2G
    reservations:
      cpus: "0.5"
      memory: 512M
pids_limit: 256
```

在 `docker-compose.override.yml` 中按 Host 能力调整。
不要把 PowerShell 并发提高到未经压测的数值。
默认 `M365_PS_MAX_CONCURRENCY=4`，每次执行会启动一个 `pwsh` 进程。
Graph Server 可以设置更严格的 CPU 和内存限制。

### 11.2 网络

Host 需要允许出站：

```text
login.microsoftonline.com
graph.microsoft.com
outlook.office365.com
ps.compliance.protection.outlook.com
*.sharepoint.com
*.teams.microsoft.com
```

生产环境应通过企业 egress proxy 或防火墙 allow-list 控制。
不要让 MCP 容器访问任意公网地址。
`nextLink` 只允许与 `https://graph.microsoft.com/v1.0` 同源，降低 SSRF 风险。

### 11.3 TLS

优先在反向代理终止 TLS。
代理到容器的连接只走私有 Docker network 或 loopback。
不要关闭 `MCP_ALLOWED_HOSTS` 校验。
不要把 8000 和 8001 直接映射到公网接口。

### 11.4 日志

容器使用 JSON stdout 日志。
Host 应把日志送入不可篡改或只追加的集中日志平台。
审计日志包含 `actor`、`action`、`correlation_id`、`approver` 和结果。
审计日志不包含业务 payload、Bearer token、证书 password 或 confirmation token。
建议至少保留与租户审计要求一致的期限。

## 12. 升级

```bash
git pull
docker compose build --pull
docker compose up -d --no-deps graph-mcp powershell-mcp
docker compose ps
```

Review 和 enabledTools 位于 Host 挂载卷，镜像升级不会覆盖。
升级前阅读 inventory diff，确认新增动作仍为 pending。
升级后先调用 `/readyz`，再执行 smoke。
证书和审批公钥轮换见 `OPERATIONS.md`。

## 13. 停止

```bash
docker compose down
```

默认保留 `valkey-data` 卷。
如果确认不再需要防重放状态，才使用：

```bash
docker compose down -v
```

删除 `valkey-data` 会让未过期 confirmation jti 丢失，必须同时撤销或等待审批 token 过期。
生产删除前必须确认审批系统已停止签发新 token 并缩短过期窗口。

## 14. 故障排查

| 症状 | 检查 |
|---|---|
| 容器启动即退出 | `docker compose logs`，优先检查 `.env` 缺项和 secrets 权限 |
| `/readyz` 返回 503 | enabledTools 或 Review 文件错误，或 hybrid search 不可用 |
| `semanticReady=false` | embedding endpoint、模型、维度或启动索引失败 |
| `searchMode=lexical_fallback` | 配置允许 fallback，当前 semantic 不可用 |
| 入口返回 401 | JWT 过期、issuer 错误、audience 错误、JWKS 不可达 |
| 入口返回 403 | 缺少 MCP app role、client ID 不在 allow-list、tenant 不符 |
| `execute_write` 返回 confirmation 错误 | token 参数摘要、actor、audience、exp 或 jti 不匹配 |
| Graph 返回 403 | upstream app 缺少 Application permission 或未 admin consent |
| Graph 返回 429 | 遵守 Graph throttle，缩小查询；Server 对 GET 有有界重试 |
| PowerShell 连接失败 | 证书 PFX password、Organization 主域名、Exchange.ManageAsApp、Entra role |
| Teams 连接失败 | 证书包含 private key、password 文件存在、Teams app-only 权限完整 |
| SharePoint 连接失败 | PnP 模块版本、SharePoint admin URL、Sites.FullControl.All、SharePoint Administrator |
| PowerShell 超时 | 缩小查询、调低并发、检查具体 cmdlet 延迟 |
