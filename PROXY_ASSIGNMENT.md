# Proxy assignment note

分配前会先做两层检查：
1. 查询 Resin `/api/v1/platforms`，只选 `routable_node_count > 0` 的 `cleanNN` 池。
2. 再通过真实代理 URL 主动请求 `https://api.ipify.org`，探测成功才会分配。

查看某个账号当前分配到哪个代理：
- `GET /v0/management/auth-files`
- 返回字段：`proxy_url`

示例：
```bash
curl -sS -H 'Authorization: Bearer zxc123' \
  'http://192.168.20.204:33133/v0/management/auth-files'
```

执行分配前先 dry-run：
```bash
python3 scripts/assign_resin_proxies.py
```

真实执行：
```bash
python3 scripts/assign_resin_proxies.py --apply
```

**紧急回滚（清除所有 proxy 分配）：**
```bash
# 先 dry-run 确认范围
python3 scripts/assign_resin_proxies.py --clear

# 真实清除
python3 scripts/assign_resin_proxies.py --clear --apply
```
