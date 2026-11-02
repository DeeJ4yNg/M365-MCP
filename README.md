# Microsoft 365 MCP Servers

两个独立运行、统一治理的 Microsoft 365 MCP Server。

| Server | 能力 | 技术栈 |
|---|---|---|
| `graph-mcp` | Entra、Intune、SharePoint、OneDrive、Teams、M365 reports、service health、security | Python 3.13、FastMCP 3.4.11、Microsoft Graph v1.0 |
| `powershell-mcp` | Exchange Online、Security & Compliance、Teams policies、SharePoint tenant admin | Python 3.13、PowerShell 7.6.9、EXO 3.10.1、Teams 8.0.0、PnP 3.4.1 |

当前 Inventory 共 298 个业务动作。
仓库基线已 Review approved 并 enable 159 个 implemented read action，占 Graph 100 个、PowerShell 59 个。
127 个 write action 和 12 个未实现 action 仍保持 disabled。
这批只读 enablement 使用 owner 批量批准，接入生产前应替换为正式的逐域 Review 证据。

## 核心设计

1. 两个 Server 独立镜像、独立容器、独立 upstream 应用身份和证书。
2. MCP transport 使用 stateless Streamable HTTP。
3. Agent 入口使用 Entra app-only JWT，校验 audience、issuer、tenant、app role 与 client allow-list。
4. 业务动作采用固定 endpoint 或固定 cmdlet，不接受任意 URL、HTTP method、脚本或 cmdlet。
5. 一个大 Catalog 通过 `search_actions`、`describe_action`、`execute_read` 和 `execute_write` 暴露，避免 298 份 Schema 常驻上下文。
6. `search_actions` 支持英文优先的词法检索和 hybrid 检索，hybrid 使用 DGX Spark 上的私有 embedding 服务和本地混合打分。
7. 写操作需要外部审批系统签发的 RS256 JWT，绑定 action、参数摘要、actor 和 jti。
8. Valkey 在上游调用前原子占用 jti，提供跨副本防重放。
9. Review registry 与 enabledTools 可通过 mtime 热加载。
10. 写操作不自动重试，失败不确定时要求对账并重新审批。
11. 审计日志不记录业务 payload、Bearer token、证书 password 或 confirmation token。

## 快速开始

```bash
uv sync --extra dev
cp .env.example .env
chmod 600 .env

./scripts/create-service-certificate.sh graph
./scripts/create-service-certificate.sh powershell
./scripts/create-service-certificate.sh agent
./scripts/create-confirmation-keypair.sh

# 按 docs/ENTRA-SETUP.md 配置 Entra 应用、权限和 app role

docker compose build graph-mcp powershell-mcp
docker compose up -d valkey graph-mcp powershell-mcp
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:8001/readyz
```

`.env.example` 默认启用 hybrid search。
部署前必须把 `MCP_SEARCH_EMBEDDING_URL` 和 health URL 指向实际的 DGX Spark embedding 服务。
如果 embedding 服务尚未部署，先把 `MCP_SEARCH_MODE` 改为 `lexical`。

批准并启用第一个只读动作：

```bash
uv run m365-inventory review \
  --server graph \
  --action entra_list_users \
  --status approved \
  --reviewer reviewer@example.com \
  --evidence CHG-2026-0001

uv run m365-inventory enable --server graph --action entra_list_users
uv run m365-inventory validate
```

详细步骤见 `docs/DEPLOYMENT.md`。

## 文档

| 文档 | 内容 |
|---|---|
| `docs/ARCHITECTURE.md` | 双 Server 架构、信任边界、读写流程、已知边界 |
| `docs/ENTRA-SETUP.md` | 上游应用、MCP resource API、app role、证书和最小权限 |
| `docs/DEPLOYMENT.md` | Docker 部署、健康检查、Review、smoke、TLS 和升级 |
| `docs/TOOL-GOVERNANCE.md` | Inventory 字段、状态语义、Review 与 enabledTools |
| `docs/API.md` | MCP tool 契约、confirmation claims、错误码和分页 |
| `docs/SECURITY.md` | 威胁模型、防重放、注入、SSRF、密钥和事故响应 |
| `docs/OPERATIONS.md` | 日常检查、证书轮换、对账、备份和下线 |
| `docs/ADDING-TOOLS.md` | 新增 Graph 或 PowerShell Tool 的开发与 Review 清单 |
| `docs/CODE-REVIEW.md` | 安全边界、阻断项、Review 顺序和记录模板 |
| `docs/TESTING.md` | 已执行测试、未验证边界和复现命令 |

架构图：`docs/architecture.svg`。

## Tool Inventory

生成物位于 `inventory/`：

```text
inventory/M365-Tool-Inventory.json
inventory/M365-Tool-Inventory.csv
inventory/M365-Tool-Inventory.md
```

重新生成：

```bash
uv run m365-inventory export --output inventory
```

当前分布：

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

## 项目结构

```text
src/m365_mcp/
  catalog.py              Catalog、词法搜索、Review 与 enabledTools 热加载
  search_lexicon.py       英文优先 normalization、短语归一与 M365 concept lexicon
  search_service.py       Hybrid 语义索引、远程 embedding 与混合打分
  confirmation.py         确认 JWT 验证与 Valkey/SQLite 防重放
  server_common.py        五个治理工具与 health routes
  graph_client.py         证书认证 Graph client、节流、分页和结果上限
  graph/                  Graph executor 与 server
  powershell/             固定 cmdlet executor、worker.ps1 与 server
  catalogs/               graph.json 与 powershell.json
config/
  enabledTools.json       默认全部空
  review-registry.json    默认全部 pending
deploy/                  两个 Dockerfile
scripts/                 证书、审批密钥、token、目录生成和 PowerShell 校验
tests/                   66 项单元与集成测试
```

## 常用命令

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run m365-inventory validate
uv run m365-inventory stats
uv run python scripts/generate_graph_catalog.py
/tmp/m365-pwsh/pwsh -File scripts/Validate-PowerShellCatalog.ps1 -ModulePath /tmp/m365-psmodules
```

PowerShell Catalog checker 需要本机安装 PowerShell 7.6 和三个固定模块版本。
它不连接 tenant，只验证 worker 语法和可在离线导入的命令参数元数据。

## 状态说明

`implemented` 表示代码路径、固定绑定和单元测试完成。
`implemented` 不表示真实 tenant 的 RBAC、API license、network path 和业务回滚已验证。
所有动作必须经过 `docs/TOOL-GOVERNANCE.md` 中的 Review 流程才能启用。
