# Proxy Allocator — Resin 代理自动分配

## 概述

`scripts/assign_resin_proxies.py` 是一个幂等的代理分配协调器，负责将 Resin 代理池中的 `cleanNN` 平台自动分配给 CLIProxyAPI 的 auth files。

每次运行都会执行完整的协调循环：
- 已分配且代理池健康 → **保持不动**
- 未分配 → 分配到负载最低的健康池
- 已分配但代理池探测失败 → **驱散并重新分配**
- 重复运行 → 零变更（幂等）

---

## 部署架构

```
[Mac 宿主机]
  ├── Resin          :12260   (代理池管理，管理 clean01-04 住宅IP节点)
  ├── xray           :10808   (socks5，备用)
  └── Docker
        └── CLIProxyAPI  :33133   (容器内，通过 host.docker.internal 访问宿主机)
```

**关键约束：** CLIProxyAPI 运行在 Docker 容器内，容器内 `127.0.0.1` 指向容器自身，访问宿主机服务必须使用 `host.docker.internal`。

因此写入 auth file 的 proxy URL 格式为：
```
http://clean01:TOKEN@host.docker.internal:12260
```

而不是：
```
http://clean01:TOKEN@192.168.20.204:12260  ← 容器内不可达
```

脚本运行在 Mac 本地，通过 `192.168.20.204:12260` 调用 Resin API 和探测代理，写入 auth file 的 proxy URL 则使用 `host.docker.internal:12260`（`--resin-proxy-base` 参数）。

---

## 前置条件

1. CLIProxyAPI 部署并设置了 `MANAGEMENT_PASSWORD`（启用远程管理 API）
2. Resin 运行且 `cleanNN` 平台有 `routable_node_count > 0` 的节点
3. CLIProxyAPI 服务端已包含 `proxy_url` 字段输出（`GET /v0/management/auth-files` 响应中每个 auth file 都含 `proxy_url` 字段）
4. Python 3.8+（仅标准库，无需安装依赖）

---

## 快速开始

```bash
cd /path/to/CLIProxyAPI-33133

# 1. 预览变更（dry-run，不修改任何数据）
python3 scripts/assign_resin_proxies.py

# 2. 真实执行
python3 scripts/assign_resin_proxies.py --apply

# 3. 紧急回滚：清除所有 proxy 分配
python3 scripts/assign_resin_proxies.py --clear
python3 scripts/assign_resin_proxies.py --clear --apply
```

---

## 参数说明

| 参数 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `--cliproxy-base` | `CLIPROXY_BASE` | `http://192.168.20.204:33133` | CLIProxyAPI 管理 API 地址 |
| `--cliproxy-key` | `CLIPROXY_KEY` | `zxc123` | 管理 API 密钥（`MANAGEMENT_PASSWORD`） |
| `--resin-base` | `RESIN_BASE` | `http://192.168.20.204:12260` | Resin API 地址（脚本本地探测用） |
| `--resin-proxy-base` | `RESIN_PROXY_BASE` | `http://host.docker.internal:12260` | 写入 auth file 的 Resin 地址（容器内访问宿主机） |
| `--resin-admin-token` | `RESIN_ADMIN_TOKEN` | `zxc13875517127` | Resin 管理 API token |
| `--resin-proxy-token` | `RESIN_PROXY_TOKEN` | `zxc13875517127` | Resin proxy 鉴权 token |
| `--pool-prefix` | `POOL_PREFIX` | `clean` | Resin 平台名前缀 |
| `--soft-cap` | `SOFT_CAP` | `70` | 每个平台软上限（超过后溢出到最少负载池） |
| `--probe-target` | `PROBE_TARGET` | `https://api.ipify.org` | 代理探测目标 URL |
| `--timeout` | `ALLOCATOR_TIMEOUT` | `8.0` | HTTP 超时秒数 |
| `--apply` | — | false | 执行实际修改（否则仅 dry-run） |
| `--clear` | — | false | 清除所有 proxy_url 分配 |
| `--verbose` | — | false | 打印每个池的探测详情 |
| `--allow-missing-proxy-field` | — | false | 跳过服务端版本检查 |

---

## 工作流程

```
1. 拉取 CLIProxyAPI auth files
        ↓
2. 拉取 Resin platforms（过滤 cleanNN 且 routable_node_count > 0）
        ↓
3. 对每个候选池通过真实 proxy URL 探测 probe-target
   → 探测失败的池不参与分配
        ↓
4. 对每个 auth file 分类：
   - healthy：proxy_url 指向健康池 → unchanged
   - reassign：proxy_url 指向不健康池 → 加入待分配队列
   - unassigned：proxy_url 为空 → 加入待分配队列
        ↓
5. 对待分配队列按名称排序后依次分配：
   - 优先选 assigned_count < soft_cap 的池
   - 同等条件下选 assigned_count 最少的池
   - 所有池均超 soft_cap 时仍选最少负载池（soft cap 不强制）
        ↓
6. 输出 JSON 报告（stdout）
        ↓
7. 若 --apply：逐条 PATCH 到管理 API
```

---

## 输出格式

```json
{
  \"mode\": \"apply\",
  \"pool_prefix\": \"clean\",
  \"healthy_pools\": [
    {
      \"name\": \"clean01\",
      \"proxy_url\": \"http://clean01:TOKEN@host.docker.internal:12260\",
      \"routable_node_count\": 1,
      \"probe\": \"http 200 b'74.211.101.20'\",
      \"assigned_count\": 29
    }
  ],
  \"summary\": {
    \"total_auth_files\": 114,
    \"unchanged\": 85,
    \"planned_changes\": 29
  },
  \"changes\": [
    {
      \"name\": \"codex-xxx.json\",
      \"from_proxy_url\": \"\",
      \"to_proxy_url\": \"http://clean01:TOKEN@host.docker.internal:12260\"
    }
  ]
}
```

---

## 管理 API 相关接口

### 查看所有 auth files 及当前 proxy 分配
```bash
curl -sS -H 'Authorization: Bearer zxc123' \\
  'http://192.168.20.204:33133/v0/management/auth-files'
```

### 手动给单个 auth file 分配 proxy
```bash
curl -sS -X PATCH \\
  -H 'Authorization: Bearer zxc123' \\
  -H 'Content-Type: application/json' \\
  -d '{\"name\":\"<auth_file_name>\",\"proxy_url\":\"http://clean01:TOKEN@host.docker.internal:12260\"}' \\
  'http://192.168.20.204:33133/v0/management/auth-files/fields'
```

### 手动清除单个 auth file 的 proxy
```bash
curl -sS -X PATCH \\
  -H 'Authorization: Bearer zxc123' \\
  -H 'Content-Type: application/json' \\
  -d '{\"name\":\"<auth_file_name>\",\"proxy_url\":\"\"}' \\
  'http://192.168.20.204:33133/v0/management/auth-files/fields'
```

### 通过 api-call 测试单个凭据的代理连通性
```bash
# 先查 auth_index
curl -sS -H 'Authorization: Bearer zxc123' \\
  'http://192.168.20.204:33133/v0/management/auth-files' | \\
  python3 -c \"import json,sys; [print(f['name'], f['auth_index']) for f in json.load(sys.stdin)['files'] if 'target' in f['name']]\"

# 测试代理连通
curl -sS -X POST \\
  -H 'Authorization: Bearer zxc123' \\
  -H 'Content-Type: application/json' \\
  -d '{\"auth_index\":\"<auth_index>\",\"method\":\"GET\",\"url\":\"https://api.ipify.org\"}' \\
  'http://192.168.20.204:33133/v0/management/api-call'
```

---

## 服务端修改说明

本功能需要 CLIProxyAPI 服务端的以下改动（已包含在当前构建中）：

### `internal/api/handlers/management/auth_files.go`

`buildAuthFileEntry` 函数中始终输出 `proxy_url` 字段（即使为空字符串），以便脚本安全检查能区分