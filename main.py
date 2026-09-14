#!/usr/bin/env python3
"""
Export cookies from a remote Chromium instance via CDP.

Two modes:

1. CLI mode:
       python cdp_cookies.py --cdp 127.0.0.1:9222 --domains youtube.com -o cookies.txt

2. API mode (aiohttp is imported lazily, only when this mode is used):
       python cdp_cookies.py --api 0.0.0.0:8080 --cdp 127.0.0.1:9222
   Then query:
       curl "http://127.0.0.1:8080/cookies?domains=youtube.com"
       curl "http://127.0.0.1:8080/cookies?cdp=10.0.0.5:9222&domains=youtube.com"
"""

import argparse
import asyncio
import datetime
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CDP helpers (sync, used by CLI mode and by worker threads in API mode)
# ---------------------------------------------------------------------------

def get_ws_url(cdp_host: str, cdp_port: int, timeout: float = 10.0) -> str:
    """Fetch the browser WebSocket URL from /json/version."""
    url = f"http://{cdp_host}:{cdp_port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach CDP at {url}: {exc}") from exc

    ws_url = data.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError(f"No webSocketDebuggerUrl in response from {url}")
    return ws_url


def _cdp_call(ws, method: str, params: Optional[Dict[str, Any]] = None, msg_id: int = 1) -> Dict[str, Any]:
    """Send one CDP command and wait for the matching response."""
    payload: Dict[str, Any] = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    ws.send(json.dumps(payload))

    while True:
        raw = ws.recv()
        if not raw:
            raise RuntimeError("CDP connection closed")
        msg = json.loads(raw)
        if msg.get("id") != msg_id:
            # Skip events
            continue
        if "error" in msg:
            raise RuntimeError(f"CDP error on {method}: {msg['error']}")
        return msg.get("result", {})


def fetch_all_cookies(cdp_host: str, cdp_port: int) -> List[Dict[str, Any]]:
    """Connect to CDP and retrieve every cookie known to the browser."""
    import websocket  # websocket-client; imported here so CLI mode has no hard dep at module load

    ws_url = get_ws_url(cdp_host, cdp_port)
    origin = f"http://{cdp_host}:{cdp_port}"
    ws = websocket.create_connection(ws_url, timeout=30, origin=origin, suppress_origin=True)
    try:
        try:
            result = _cdp_call(ws, "Network.getAllCookies")
        except RuntimeError as exc:
            if "'Network.getAllCookies' wasn't found" in str(exc):
                result = _cdp_call(ws, "Storage.getCookies", {"partitionKey": "unrestricted"})
                cookies = result.get("cookiesData", result.get("cookies", []))
                return [_normalize_storage_cookie(c) for c in cookies]
            raise
    finally:
        ws.close()
    return result.get("cookies", [])


def _normalize_storage_cookie(c: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Storage.getCookies cookie to the same shape as Network.getAllCookies."""
    return {
        "name": c.get("name", ""),
        "value": c.get("value", ""),
        "domain": c.get("domain", ""),
        "path": c.get("path", "/"),
        "secure": c.get("secure", False),
        "httpOnly": c.get("httpOnly", False),
        "expires": c.get("expirationDate", 0),
    }


# ---------------------------------------------------------------------------
# Domain filtering + Netscape serialization
# ---------------------------------------------------------------------------

def parse_domains(raw: str) -> List[str]:
    """Split a domain string by comma or colon; drop empties."""
    if not raw or not raw.strip():
        return []
    normalized = raw.replace(":", ",")
    return [d.strip() for d in normalized.split(",") if d.strip()]


def domain_matches(cookie_domain: str, target: str) -> bool:
    """Match a cookie domain against a target, handling leading dots and subdomains."""
    cd = cookie_domain.lstrip(".").lower()
    td = target.lstrip(".").lower()
    return cd == td or cd.endswith("." + td)


def filter_cookies(cookies: List[Dict[str, Any]], domains: List[str]) -> List[Dict[str, Any]]:
    if not domains:
        return cookies
    return [c for c in cookies if any(domain_matches(c.get("domain", ""), d) for d in domains)]


def to_netscape(cookies: List[Dict[str, Any]]) -> str:
    """Serialize cookies to the Netscape HTTP Cookie File format (used by yt-dlp, curl)."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated automatically via CDP",
        "# Format: domain  flag  path  secure  expiry  name  value",
        "",
    ]
    for c in cookies:
        domain = c.get("domain", "")
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires", 0)) if c.get("expires", 0) and c.get("expires", 0) > 0 else 0
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def _cookie_filename(domain: str) -> str:
    """Generate output filename for a single domain: domain.cookies.YYYYMMDD_HHMMSS."""
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime("%Y%m%d_%H%M%S")
    return f"{domain}.cookies.{ts}"


def run_cli(args: argparse.Namespace) -> int:
    if ":" not in args.cdp:
        print("Error: --cdp must be in host:port form", file=sys.stderr)
        return 2
    host, port_str = args.cdp.rsplit(":", 1)
    try:
        port = int(port_str)
    except ValueError:
        print(f"Error: invalid port '{port_str}'", file=sys.stderr)
        return 2

    domains = parse_domains(args.domains)

    try:
        all_cookies = fetch_all_cookies(host, port)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3

    print(f"Total cookies fetched: {len(all_cookies)}", file=sys.stderr)

    written_count = 0
    for domain in domains:
        filtered = filter_cookies(all_cookies, [domain])
        if not filtered:
            print(f"Warning: no matching cookies found for domain '{domain}'.", file=sys.stderr)
            continue

        filename = _cookie_filename(domain)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(to_netscape(filtered))

        print(f"Written {len(filtered)} cookies to: {filename}", file=sys.stderr)
        written_count += 1

    if not domains:
        print("Warning: no domains specified, no files written.", file=sys.stderr)
        return 1

    print(f"Files written: {written_count}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# API mode (aiohttp imported lazily)
# ---------------------------------------------------------------------------

def _parse_cdp_arg(value: str) -> tuple:
    if ":" not in value:
        raise ValueError("cdp must be in host:port form")
    host, port_str = value.rsplit(":", 1)
    return host, int(port_str)


def run_api(args: argparse.Namespace) -> int:
    from aiohttp import web  # lazy import: only needed in API mode

    if ":" not in args.api:
        print("Error: --api must be in host:port form", file=sys.stderr)
        return 2
    api_host, api_port_str = args.api.rsplit(":", 1)
    try:
        api_port = int(api_port_str)
    except ValueError:
        print(f"Error: invalid API port '{api_port_str}'", file=sys.stderr)
        return 2

    # Optional default CDP endpoint, used when a request omits ?cdp=
    default_cdp: Optional[str] = args.cdp or None
    if default_cdp is not None and ":" not in default_cdp:
        print("Error: --cdp must be in host:port form", file=sys.stderr)
        return 2

    async def handle_cookies(request: "web.Request") -> "web.Response":
        """
        GET /cookies[?cdp=HOST:PORT][&domains=a,b,c][&format=netscape|json]

        Query params:
            cdp     (optional) - override the default CDP endpoint (host:port).
                                 If omitted, the value passed via --cdp at startup is used.
            domains (optional) - comma- or colon-separated domains; empty => all cookies
            format  (optional) - "netscape" (default) or "json"
        """
        cdp_arg = request.query.get("cdp") or default_cdp
        if not cdp_arg:
            return web.json_response(
                {"error": "missing CDP endpoint: pass ?cdp=HOST:PORT or start the server with --cdp"},
                status=400,
            )

        try:
            cdp_host, cdp_port = _parse_cdp_arg(cdp_arg)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        domains = parse_domains(request.query.get("domains", ""))
        fmt = request.query.get("format", "netscape").lower()

        # The CDP client is synchronous; run it in a thread so we don't block the event loop.
        try:
            all_cookies = await asyncio.to_thread(fetch_all_cookies, cdp_host, cdp_port)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=502)

        filtered = filter_cookies(all_cookies, domains)

        if fmt == "json":
            return web.json_response({
                "cdp": f"{cdp_host}:{cdp_port}",
                "domains": domains,
                "count": len(filtered),
                "cookies": filtered,
            })

        if fmt != "netscape":
            return web.json_response({"error": f"unknown format '{fmt}'"}, status=400)

        return web.Response(
            text=to_netscape(filtered),
            content_type="text/plain",
            charset="utf-8",
        )

    async def handle_health(request: "web.Request") -> "web.Response":
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get("/cookies", handle_cookies)
    app.router.add_get("/health", handle_health)

    print(f"Listening on http://{api_host}:{api_port}", file=sys.stderr)
    if default_cdp:
        print(f"Default CDP endpoint: {default_cdp}", file=sys.stderr)
    else:
        print("No default CDP endpoint set; ?cdp=HOST:PORT is required per request.", file=sys.stderr)
    print("  GET /cookies[?cdp=HOST:PORT][&domains=a,b][&format=netscape|json]", file=sys.stderr)
    print("  GET /health", file=sys.stderr)

    web.run_app(app, host=api_host, port=api_port, print=None)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _env_or_none(name: str) -> Optional[str]:
    """Return env value if set and non-empty, else None."""
    val = os.environ.get(name, "")
    return val if val else None


def parse_args() -> argparse.Namespace:
    # Read environment variables for docker-compose integration
    cdp_env = _env_or_none("CDP")
    cdp_host_env = _env_or_none("CDP_HOST")
    cdp_port_env = _env_or_none("CDP_PORT")
    api_env = _env_or_none("API")
    api_host_env = _env_or_none("API_HOST")
    api_port_env = _env_or_none("API_PORT")

    # Build host:port from separate HOST/PORT env vars if available
    if cdp_host_env and cdp_port_env:
        cdp_env = f"{cdp_host_env}:{cdp_port_env}"
    if api_host_env and api_port_env:
        api_env = f"{api_host_env}:{api_port_env}"

    p = argparse.ArgumentParser(
        description="Export Chromium cookies via CDP (CLI or HTTP API mode)."
    )
    p.add_argument(
        "--cdp", "-c",
        default=cdp_env,
        help="CDP endpoint in host:port form. "
             "In CLI mode: required, the source of cookies. "
             "In API mode: optional, the default endpoint used when a request omits ?cdp=. "
             f"Env: CDP, or CDP_HOST + CDP_PORT.",
    )
    p.add_argument(
        "--domains", "-d",
        default="",
        help="Comma- or colon-separated domains (CLI mode). "
             "One cookie file is created per domain, named: domain.cookies.YYYYMMDD_HHMMSS",
    )
    p.add_argument(
        "--output", "-o",
        default="",
        help="Output file path (CLI mode, legacy). When empty, filenames are generated "
             "automatically per domain: domain.cookies.YYYYMMDD_HHMMSS",
    )
    p.add_argument(
        "--api", "-a",
        default=api_env,
        help="Run as HTTP API server on host:port (API mode). "
             "aiohttp is imported only when this option is used. "
             f"Env: API, or API_HOST + API_PORT.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.api:
        return run_api(args)
    if args.cdp:
        return run_cli(args)

    # Auto-detect mode when env vars are set but no CLI args given.
    cdp_env = _env_or_none("CDP") or None
    cdp_host_env = _env_or_none("CDP_HOST")
    cdp_port_env = _env_or_none("CDP_PORT")
    api_env = _env_or_none("API") or None
    api_host_env = _env_or_none("API_HOST")
    api_port_env = _env_or_none("API_PORT")

    if cdp_host_env and cdp_port_env:
        cdp_env = f"{cdp_host_env}:{cdp_port_env}"
    if api_host_env and api_port_env:
        api_env = f"{api_host_env}:{api_port_env}"

    if api_env:
        return run_api(args)
    if cdp_env:
        return run_cli(args)

    print("Error: specify either --cdp (CLI mode) or --api (API mode).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
