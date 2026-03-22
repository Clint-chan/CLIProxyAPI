# scripts/

## assign_resin_proxies.py

将 Resin 代理池中的 `cleanNN` 平台分配给 CLIProxyAPI 的 auth files。

### 架构说明

- CLIProxyAPI 运行在 **Docker 容器**内
- Resin 运行在**宿主机**（`192.168.20.204:12260`）
- 容器内访问宿主机必须用 `host.docker.internal`，不能用 `127.0.0.1` 或 `192.168.20.204`
- 脚本从 Mac 本地运行，用 `192.168.20.204:12260` 调 Resin API；写入 auth file 的 proxy URL 用 `host.docker.internal:12260`

### 依赖

- Python 3.8+（仅标准库）
- CLIProxyAPI 已部署并开启 `MANAGEMENT_PASSWORD`
- Resin 已启动且 `cleanNN` 平台有可路由节点

### 常用命令

```bash
# Dry-run（预览，不修改）
python3 scripts/assign_resin_proxies.py

# 真实执行
python3 scripts/assign_resin_proxies.py --apply

# 紧急回滚：清除所有 proxy 分配（先 dry-run 确认）
python3 scripts/assign_resin_proxies.py --clear
python3 scripts/assign_resin_proxies.py --clear --apply

# 详细模式（显示每个池的探测结果）
python3 scripts/assign_resin_proxies.py --verbose
```

### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--cliproxy-base` | `http://192.168.20.204:33133` | CLIProxyAPI 管理 API 地址 |
| `--cliproxy-key` | `zxc123` | 管理 API 密钥 |
| `--resin-base` | `http://192.168.20.204:12260` | Resin API 地址（脚本本地访问） |
| `--resin-proxy-base` | `http://host.docker.internal:12260` | 写入 auth file 的 Resin proxy 地址（容器内访问） |
| `--pool-prefix` | `clean` | Resin 平台名前缀 |
| `--soft-cap` | `70` | 每个平台最多分配的 auth file 数 |
| `--apply` | false | 真实执行（否则只 dry-run） |
| `--clear` | false | 清除所有 proxy 分配 |

所有参数均可通过同名大写环境变量覆盖（如 `RESIN_BASE=...`）。

### 工作原理

1. 从 Resin `/api/v1/platforms` 获取 `cleanNN` 池，过滤 `routable_node_count > 0`
2. 对每个池通过真实 proxy URL 探测 `https://api.ipify.org`，确认可用
3. 读取 CLIProxyAPI 所有 auth files 的当前 `proxy_url`
4. 分类：
   - `healthy`：已分配且池仍可用 → 不动
   - `reassign`：已分配但池不健康 → 重新分配
   - `unassigned`：未分配 → 分配
5. 按最少负载优先选池，超过 soft-cap 后仍可溢出分配
6. 输出 JSON 报告；`--apply` 时逐条 PATCH 到管理 API

### 测试单个凭据

分配前可用 `api-call` 接口测试链路：

```bash
# 给单个 auth file 临时分配
curl -sS -X PATCH -H 'Authorization: Bearer zxc123' -H 'Content-Type: application/json' \
  -d '{"name":"<auth_file_name>","proxy_url":"http://clean01:zxc13875517127@host.docker.internal:12260"}' \
  'http://192.168.20.204:33133/v0/management/auth-files/fields'

# 通过该凭据测试目标 URL
curl -sS -X POST -H 'Authorization: Bearer zxc123' -H 'Content-Type: application/json' \
  -d '{"auth_index":"<auth_index>","method":"GET","url":"https://api.ipify.org"}' \
  'http://192.168.20.204:33133/v0/management/api-call'

# 确认后清除
curl -sS -X PATCH -H 'Authorization: Bearer zxc123' -H 'Content-Type: application/json' \
  -d '{"name":"<auth_file_name>","proxy_url":""}' \
  'http://192.168.20.204:33133/v0/management/auth-files/fields'
```
