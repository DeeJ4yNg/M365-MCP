# 测试与验证记录

## 1. 测试原则

1. 先验证执行边界，再验证业务成功路径。
2. 使用 fake upstream 做确定性单元测试。
3. 使用真实 Streamable HTTP 子进程做端到端测试。
4. 不把 fake tenant 结果当成真实 Microsoft 365 验收。
5. 不把 Dockerfile 静态解析当成镜像构建成功。
6. 不隐藏无法执行的测试，明确记录未验证边界。

## 2. 已执行结果

| 验证项 | 结果 |
|---|---|
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 通过 |
| `uv run pytest` | 66 passed |
| `uv build` | sdist 与 wheel 构建通过 |
| wheel runtime assets | `graph.json`、`powershell.json`、`worker.ps1`、`search_service.py`、`search_lexicon.py` 均存在 |
| Wheel version | `m365_mcp_servers-0.2.0` |
| Graph Catalog validate | 181 actions，0 schema error |
| PowerShell Catalog validate | 117 actions，105 implemented，12 planned/unsupported |
| PowerShell worker syntax | 通过 `Parser::ParseFile` |
| PowerShell local cmdlet metadata | 36 actions 参数元数据通过 |
| PowerShell connection cmdlets | 4 个 Connect cmdlet 的关键参数元数据通过 |
| PowerShell remote cmdlet metadata | 69 actions 需要 tenant session 才能完整验证 |
| Streamable HTTP E2E | 子进程真实 HTTP 连接、tools/list、execute_read 通过 |
| Compose schema | 通过 Compose Spec 官方 JSON Schema |
| Docker base image tags | 4 个 tag 在 registry 中存在 |
| 证书脚本 | RSA 4096 + PFX + password 生成和读取通过 |
| 审批密钥脚本 | RSA 3072 公私钥匹配通过 |
| Docker image build | 未执行，当前环境没有 Docker daemon |
| Microsoft 365 真实 tenant | 未执行，没有 tenant 凭据 |

测试结果对应交付时工作区。
后续代码变更必须重新执行本文命令。

## 3. 覆盖范围

### 3.1 Catalog

| 测试 | 覆盖 |
|---|---|
| 默认拒绝 | pending + enabledTools 不允许启动 |
| 未知 action | enabledTools typos 失败关闭 |
| Schema 额外字段 | `additionalProperties=false` |
| Schema 越界 | minimum 校验 |
| 搜索 | 默认排除 disabled |
| operation mismatch | read 与 write 入口不可混用 |
| 热加载 | enabledTools 变更后下一次调用生效 |
| Review 格式 | 缺 reviewer、时间、evidence 失败 |
| 真实 catalog | 数量、唯一名、域覆盖、无默认 enabled |
| JSON Schema 合法性 | `Draft202012Validator.check_schema` |
| 中英检索 | 中文意图、domain alias、无匹配不返回无关兜底 |
| English ticket search | CamelCase、phrase normalization、缩写和 ticket 症状 |
| Concept lexicon | MFA、DL、wipe、Autopilot、SharePoint access、mail flow、hint normalization |
| Hybrid search | semantic rerank、query cache、fallback、dimension 校验 |
| Embedding API | OpenAI-compatible batch、index 排序、Authorization、异常维度 |
| Hybrid MCP | `search_actions` 返回 `search_mode=hybrid` 和 semantic score |

### 3.2 认证与授权

| 测试 | 覆盖 |
|---|---|
| Read role | `M365.MCP.Read` |
| Write role | `M365.MCP.ReadWrite` |
| read 不能写 | 缺 write role 被拒绝 |
| 调用方 allow-list | 非允许 client ID 被拒绝 |
| tenant | 跨 tenant token 被拒绝 |
| insecure mode | 必须显式 allow |

### 3.3 Confirmation

| 测试 | 覆盖 |
|---|---|
| action 绑定 | action 不同被拒绝 |
| 参数绑定 | canonical params digest |
| actor 绑定 | 不同 Agent 被拒绝 |
| 过期 | exp 被拒绝 |
| 重放 | 同一 jti 第二次被拒绝 |
| 算法固定 | alg=none 被拒绝 |
| 错误密钥 | 签名错误被拒绝 |
| RS256 | production algorithm 通过 |
| HS256 | 仅显式 dev opt-in |
| SQLite | 单主机原子占用 |

### 3.4 Graph

| 测试 | 覆盖 |
|---|---|
| GET 429 | 有界重试 |
| POST 503 | 不允许自动重试 |
| 分页 | same-origin nextLink 聚合 |
| 恶意 nextLink | 拒绝 foreign origin |
| text CSV | content type 和 truncation |
| query | boolean、array、ConsistencyLevel |
| path encode | slash 等参数 URL encode |
| body map | 字段映射与常量 |
| body_const placeholder | 安全替换 |
| payload | typed body 原样传递 |
| 大结果 | read 拒绝、write 返回成功摘要 |
| PFX | MSAL 兼容格式和 SHA-1 thumbprint |
| 证书错误 | 不泄漏 password |

### 3.5 PowerShell

| 测试 | 覆盖 |
|---|---|
| 参数映射 | 业务参数到 cmdlet 参数 |
| constant | 调用方不能覆盖 |
| 超时 | 杀 worker 进程组 |
| 协议错误 | 不把无 envelope 当成功 |
| 大结果 | read 拒绝、write 摘要 |
| worker 语法 | 真实 PowerShell parser |
| 本地模块 | Teams、PnP、EXO built-in 参数元数据 |
| 未连接命令 | 列出 69 个 unverified，不当作通过 |

### 3.6 MCP

| 测试 | 覆盖 |
|---|---|
| Tool 面 | 五个治理工具 |
| annotations | title、readOnlyHint、destructiveHint |
| search | enabled action 返回 Schema |
| execute_read | 真实 executor 路径 |
| execute_write | 缺 confirmation 拒绝 |
| 写成功 | 合法 confirmation |
| 写重放 | 第二次失败 |
| health | `/healthz`、`/readyz` |
| response headers | no-store、nosniff、no-referrer |
| HTTP | 独立子进程 Streamable HTTP E2E |
| JWT ingress | 本地 JWKS、RS256 token、app role 与 tenant 校验 |
| PowerShell server | 同一治理逻辑 |

## 4. 复现命令

### 4.1 Python 测试

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

如果 Host 的临时目录有非常规挂载限制，可指定：

```bash
uv run pytest --basetemp=/tmp/m365-mcp-pytest
```

### 4.2 Wheel

```bash
rm -rf dist
uv build
python3 - <<'PY'
import glob
import zipfile

wheel = glob.glob("dist/*.whl")[0]
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
for wanted in (
    "m365_mcp/catalogs/graph.json",
    "m365_mcp/catalogs/powershell.json",
    "m365_mcp/powershell/worker.ps1",
    "m365_mcp/search_service.py",
    "m365_mcp/search_lexicon.py",
):
    assert wanted in names, wanted
print("wheel assets ok")
PY
```

### 4.3 Inventory

```bash
uv run m365-inventory validate
uv run m365-inventory stats
uv run m365-inventory export --output inventory
```

### 4.4 PowerShell

安装 PowerShell 7.6 和固定模块：

```bash
pwsh -NoProfile -Command \
  "Install-Module ExchangeOnlineManagement -RequiredVersion 3.10.1 -Scope CurrentUser -Force; \
   Install-Module MicrosoftTeams -RequiredVersion 8.0.0 -Scope CurrentUser -Force; \
   Install-Module PnP.PowerShell -RequiredVersion 3.4.1 -Scope CurrentUser -Force"
```

执行 metadata 检查：

```bash
pwsh -NoProfile -File scripts/Validate-PowerShellCatalog.ps1
```

检查脚本不会连接 Microsoft 365。
无法离线导入的 Exchange remote cmdlets 会进入 `unverified`。
真正 tenant 验证必须在测试 tenant 执行。

### 4.5 Hybrid search

```bash
uv run pytest tests/test_hybrid_search.py -s
```

这些测试使用 fake embedding provider 和 mock OpenAI-compatible HTTP，不访问 DGX Spark。
真实 embedding 服务的兼容性、模型质量和并发延迟仍需在目标环境测试。

### 4.6 Streamable HTTP

```bash
uv run pytest tests/test_streamable_http.py -s
```

该测试会启动真实 Python 子进程、监听 loopback 端口、通过 FastMCP Client 连接 `/mcp`。
它使用 fake pwsh worker，不访问 Microsoft 365。

### 4.7 Docker

有 Docker daemon 的机器执行：

```bash
cp .env.example .env
# 填写非 secret 配置
# 放入 docs/DEPLOYMENT.md 中列出的 secrets

docker compose build graph-mcp powershell-mcp
docker compose up -d valkey graph-mcp powershell-mcp
docker compose ps
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8001/readyz
```

当前交付环境没有 Docker daemon，以上镜像构建和容器启动未执行。

## 5. 必须在真实 tenant 完成的验证

以下项目不能由单元测试替代：

1. 四个 Entra 应用和两个 MCP resource API 的实际 app role 配置。
2. Agent app-only token 的 audience、roles 和 tenant。
3. Graph upstream 的最小权限和 admin consent。
4. ExchangeOnlineManagement 证书登录。
5. Security & Compliance PowerShell 登录。
6. Teams PowerShell app-only 登录。
7. PnP.PowerShell 证书登录。
8. 69 个 remote Exchange cmdlet 的参数和返回格式。
9. Microsoft Graph 每个 endpoint 在商业云 tenant 的实际响应。
10. Intune 许可和 Graph API 支持范围。
11. SharePoint site、list、drive 的访问范围。
12. 写操作的业务回滚。
13. 审批系统签发的实际 token。
14. Docker 镜像在目标 Linux Host 的网络、证书和 secrets 挂载。

## 6. 未验证与已知缺口

| 缺口 | 影响 | 后续动作 |
|---|---|---|
| 没有 Docker daemon | 镜像构建和容器运行未证实 | 在目标 Host 构建并跑 smoke |
| 没有真实 tenant | Microsoft API 权限和数据行为未证实 | 使用隔离 tenant 按验收顺序测试 |
| 69 个 remote PowerShell cmdlet | 参数元数据未离线验证 | 在实际 tenant 会话执行 `Get-Command` 对比 |
| 未验证 DGX Spark embedding 服务 | TEI 121 experimental 的 GB10 行为和并发未验证 | 运行 compatibility/latency benchmark |
| Hybrid 阈值未校准 | `semantic_floor`、`ceiling` 和权重只是初始值 | 用真实运维问句 gold set 调参 |
| 未接入审批系统 | 只提供 local mint CLI 和 token 契约 | 实现 Change 系统签发集成 |
| 未实现多 key confirmation | 轮换需要停签等待 TTL | 后续支持 JWKS/kid |
| 未实现多 tenant 路由 | 单部署只服务一个 tenant | 多实例部署或增加 tenant 路由 |
| 未做性能基准 | 并发和 timeout 是保守默认值 | 在目标 Host 压测 |

## 7. 发布门槛

发布前最低门槛：

1. Python tests 全通过。
2. Ruff check 和 format check 全通过。
3. Catalog validate 全通过。
4. Inventory 重新导出。
5. 新动作仍为 pending。
6. Docker build 成功。
7. 容器 `/readyz` 成功。
8. 两个 Server 的 smoke 成功。
9. 测试 tenant 的只读动作成功。
10. 写动作有 Review evidence 和回滚记录。
