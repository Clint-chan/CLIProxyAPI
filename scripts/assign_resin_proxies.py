#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from typing import Any


DEFAULT_CLIPROXY_BASE = "http://192.168.20.204:33133"
DEFAULT_CLIPROXY_KEY = "zxc123"
DEFAULT_RESIN_BASE = "http://192.168.20.204:12260"
DEFAULT_RESIN_ADMIN_TOKEN = "zxc13875517127"
DEFAULT_RESIN_PROXY_TOKEN = "zxc13875517127"
DEFAULT_POOL_PREFIX = "clean"
DEFAULT_SOFT_CAP = 70
DEFAULT_PROBE_TARGET = "https://api.ipify.org"
DEFAULT_TIMEOUT = 8.0
POOL_NAME_RE = re.compile(r"^(?P<prefix>[a-zA-Z]+)(?P<index>\d+)$")


@dataclass(frozen=True)
class Pool:
    name: str
    proxy_url: str
    routable_node_count: int


@dataclass
class AuthFile:
    name: str
    proxy_url: str
    status: str
    unavailable: bool
    proxy_field_present: bool


class HttpClient:
    def __init__(self, timeout: float, default_headers: dict[str, str] | None = None):
        self.timeout = timeout
        self.default_headers = default_headers or {}

    def request_json(self, method: str, url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        body = None
        merged_headers = dict(self.default_headers)
        if headers:
            merged_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            merged_headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, method=method, headers=merged_headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def request_text(self, method: str, url: str, headers: dict[str, str] | None = None) -> str:
        merged_headers = dict(self.default_headers)
        if headers:
            merged_headers.update(headers)
        req = urllib.request.Request(url, method=method, headers=merged_headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign Resin proxy URLs to CLIProxyAPI auth files")
    parser.add_argument("--cliproxy-base", default=os.getenv("CLIPROXY_BASE", DEFAULT_CLIPROXY_BASE))
    parser.add_argument("--cliproxy-key", default=os.getenv("CLIPROXY_KEY", DEFAULT_CLIPROXY_KEY))
    parser.add_argument("--resin-base", default=os.getenv("RESIN_BASE", DEFAULT_RESIN_BASE))
    parser.add_argument("--resin-admin-token", default=os.getenv("RESIN_ADMIN_TOKEN", DEFAULT_RESIN_ADMIN_TOKEN))
    parser.add_argument("--resin-proxy-token", default=os.getenv("RESIN_PROXY_TOKEN", DEFAULT_RESIN_PROXY_TOKEN))
    parser.add_argument("--pool-prefix", default=os.getenv("POOL_PREFIX", DEFAULT_POOL_PREFIX))
    parser.add_argument("--soft-cap", type=int, default=int(os.getenv("SOFT_CAP", str(DEFAULT_SOFT_CAP))))
    parser.add_argument("--probe-target", default=os.getenv("PROBE_TARGET", DEFAULT_PROBE_TARGET))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("ALLOCATOR_TIMEOUT", str(DEFAULT_TIMEOUT))))
    parser.add_argument("--apply", action="store_true", help="apply changes instead of dry run")
    parser.add_argument("--allow-missing-proxy-field", action="store_true", help="allow execution even if management API does not return proxy_url fields")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def pool_sort_key(name: str) -> tuple[int, str]:
    match = POOL_NAME_RE.match(name)
    if not match:
        return (10**9, name)
    return (int(match.group("index")), name)


def resin_proxy_url(public_base: str, pool_name: str, proxy_token: str) -> str:
    parsed = urllib.parse.urlparse(public_base)
    scheme = parsed.scheme or "http"
    host = parsed.netloc or parsed.path
    return f"{scheme}://{pool_name}:{proxy_token}@{host}"


def fetch_auth_files(client: HttpClient, base_url: str) -> list[AuthFile]:
    payload = client.request_json("GET", join_url(base_url, "/v0/management/auth-files"))
    files = payload.get("files", [])
    auths: list[AuthFile] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        auths.append(
            AuthFile(
                name=name,
                proxy_url=str(item.get("proxy_url", "")).strip(),
                status=str(item.get("status", "")).strip(),
                unavailable=bool(item.get("unavailable", False)),
                proxy_field_present="proxy_url" in item,
            )
        )
    return auths


def fetch_resin_pools(client: HttpClient, resin_base: str, pool_prefix: str, proxy_token: str) -> list[Pool]:
    payload = client.request_json("GET", join_url(resin_base, "/api/v1/platforms?limit=500&sort_by=name&sort_order=asc"))
    items = payload.get("items", [])
    pools: list[Pool] = []
    prefix_lower = pool_prefix.lower()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        match = POOL_NAME_RE.match(name)
        if not match:
            continue
        if match.group("prefix").lower() != prefix_lower:
            continue
        routable = int(item.get("routable_node_count", 0) or 0)
        if routable <= 0:
            continue
        pools.append(
            Pool(
                name=name,
                proxy_url=resin_proxy_url(resin_base, name, proxy_token),
                routable_node_count=routable,
            )
        )
    pools.sort(key=lambda p: pool_sort_key(p.name))
    return pools


def probe_proxy(proxy_url: str, probe_target: str, timeout: float) -> tuple[bool, str]:
    proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(proxy_handler)
    request = urllib.request.Request(probe_target, headers={"User-Agent": "resin-allocator/1.0"})
    try:
        with opener.open(request, timeout=timeout) as resp:
            body = resp.read(128)
            if 200 <= resp.status < 400:
                return True, f"http {resp.status} {body[:32]!r}"
            return False, f"http {resp.status}"
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 400:
            return True, f"http {exc.code}"
        return False, f"http error {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def patch_auth_proxy(client: HttpClient, base_url: str, auth_name: str, proxy_url: str) -> None:
    client.request_json(
        "PATCH",
        join_url(base_url, "/v0/management/auth-files/fields"),
        payload={"name": auth_name, "proxy_url": proxy_url},
    )


def classify(auth: AuthFile, healthy_proxy_urls: set[str]) -> str:
    if not auth.proxy_url:
        return "unassigned"
    if auth.proxy_url in healthy_proxy_urls:
        return "healthy"
    return "reassign"


def choose_pool(counts: Counter[str], healthy_pools: list[Pool], soft_cap: int) -> Pool:
    below_cap = [pool for pool in healthy_pools if counts[pool.proxy_url] < soft_cap]
    candidates = below_cap if below_cap else healthy_pools
    return min(candidates, key=lambda pool: (counts[pool.proxy_url], pool_sort_key(pool.name)))


def main() -> int:
    args = parse_args()

    management_client = HttpClient(
        timeout=args.timeout,
        default_headers={
            "Authorization": f"Bearer {args.cliproxy_key}",
            "Accept": "application/json",
        },
    )
    resin_client = HttpClient(
        timeout=args.timeout,
        default_headers={
            "Authorization": f"Bearer {args.resin_admin_token}",
            "Accept": "application/json",
        },
    )

    try:
        auth_files = fetch_auth_files(management_client, args.cliproxy_base)
        pools = fetch_resin_pools(resin_client, args.resin_base, args.pool_prefix, args.resin_proxy_token)
    except urllib.error.HTTPError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1

    if not pools:
        print(f"no routable pools found for prefix {args.pool_prefix!r}", file=sys.stderr)
        return 2

    if auth_files and not any(auth.proxy_field_present for auth in auth_files) and not args.allow_missing_proxy_field:
        print(
            "management API response does not include proxy_url; deploy the server change first or rerun with --allow-missing-proxy-field",
            file=sys.stderr,
        )
        return 4

    probe_results: dict[str, tuple[bool, str]] = {}
    healthy_pools: list[Pool] = []
    for pool in pools:
        ok, detail = probe_proxy(pool.proxy_url, args.probe_target, args.timeout)
        probe_results[pool.proxy_url] = (ok, detail)
        if ok:
            healthy_pools.append(pool)
        if args.verbose:
            print(f"probe {pool.name}: {'ok' if ok else 'fail'} - {detail}")

    if not healthy_pools:
        print("no healthy pools available after active probing", file=sys.stderr)
        return 3

    healthy_pools.sort(key=lambda p: pool_sort_key(p.name))
    healthy_proxy_urls = {pool.proxy_url for pool in healthy_pools}

    counts = Counter()
    unchanged: list[str] = []
    to_assign: list[AuthFile] = []

    for auth in auth_files:
        state = classify(auth, healthy_proxy_urls)
        if state == "healthy":
            counts[auth.proxy_url] += 1
            unchanged.append(auth.name)
        else:
            to_assign.append(auth)

    planned: list[tuple[str, str, str]] = []
    for auth in sorted(to_assign, key=lambda a: a.name.lower()):
        pool = choose_pool(counts, healthy_pools, args.soft_cap)
        counts[pool.proxy_url] += 1
        planned.append((auth.name, auth.proxy_url, pool.proxy_url))

    print(json.dumps(
        {
            "mode": "apply" if args.apply else "dry-run",
            "pool_prefix": args.pool_prefix,
            "healthy_pools": [
                {
                    "name": pool.name,
                    "proxy_url": pool.proxy_url,
                    "routable_node_count": pool.routable_node_count,
                    "probe": probe_results[pool.proxy_url][1],
                    "assigned_count": counts[pool.proxy_url],
                }
                for pool in healthy_pools
            ],
            "summary": {
                "total_auth_files": len(auth_files),
                "unchanged": len(unchanged),
                "planned_changes": len(planned),
            },
            "changes": [
                {
                    "name": name,
                    "from_proxy_url": old_proxy,
                    "to_proxy_url": new_proxy,
                }
                for name, old_proxy, new_proxy in planned
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))

    if not args.apply:
        return 0

    failures = 0
    for name, _old_proxy, new_proxy in planned:
        try:
            patch_auth_proxy(management_client, args.cliproxy_base, name, new_proxy)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"failed to patch {name}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
