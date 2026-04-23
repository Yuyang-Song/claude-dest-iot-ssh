#!/usr/bin/env python3
"""
Claude Buddy Serial — Linux 侧桥(远端 claude code + 本地 Windows 硬件版)
============================================================================

设计为 /volume/pt-dev/users/yuyang/config/claw-jump 的兄弟进程。
两边逻辑完全同构,区别只在干的活:

    claw-jump:     Linux hook →[SSH RF 47653]→ Windows claw_agent.py →(winotify)→ toast
    buddy-serial:  Linux bridge →[SSH RF 47654]→ Windows buddy_serial_agent.py →(pyserial)→ M5StickC Plus

本脚本的职责:
  1) 扫 Linux 本地 ~/.claude/projects/**/*.jsonl + ~/.codex/sessions/**/*.jsonl,
     推断当前是 idle / busy / attention / sleep,构造和原 BLE 协议一致的
     {total,running,waiting,msg,entries} 状态包。
  2) 每 0.8s 把状态包 POST 到 Windows agent 的 /state 端点(fire-and-forget,
     短超时 0.5s,SSH 隧道断了绝不阻塞本进程)。
  3) 暴露 HTTP 127.0.0.1:19191/permission 给 claude code 的 hook_permission.py 用。
     收到请求后转发到 Windows agent 的 /prompt 端点(阻塞,最长 ~60s),
     等硬件按键后返回 allow/deny,再回给 hook。

为什么不直接复用 tools/serial_bridge.py?
  - 原脚本绑定 pyserial,必须跑在接着 USB 的那台机(Windows)
  - 你现在 claude code 跑在远端 Linux,jsonl 在远端,串口在本地 Windows
  - 所以硬拆成两个进程:本地负责串口,远端负责扫 jsonl + hook 接入

SSH 配置:
  在 ~/.ssh/config 里(Windows 那侧)给对应 host 加:
    RemoteForward 47654 127.0.0.1:47654
  已经有 RemoteForward 47653(claw-jump)的话,并排加这行即可。

用法:
  python serial_bridge_linux.py
  python serial_bridge_linux.py --agent-port 47654 --http-port 19191
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

# ─────────── 可调参数 ───────────

STALE_SECS = 5.0            # 最近一次事件距今超过这个就算 attention(如果还在 tool_use)
SLEEP_SECS = 30.0           # 超过这个算 sleep
STATE_POLL_SECS = 0.8       # 状态刷新间隔(和原协议一致)
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 19191   # 给 hook_permission.py 的端点(保持和原项目兼容)
DEFAULT_AGENT_HOST = "127.0.0.1"
DEFAULT_AGENT_PORT = 47654  # Windows agent 的端点(通过 SSH RF 映射)
STATE_POST_TIMEOUT_S = 0.5  # /state 短超时,隧道断了立刻放弃
PROMPT_POST_TIMEOUT_S = 65.0  # /prompt 长阻塞,略大于 agent 那边 60s 默认


def now_ts() -> float:
    return time.time()


def iso_to_ts(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


# ─────────── jsonl 尾部解析 / 状态推断(和原版一致) ───────────


def looks_like_tool_use(obj: Any) -> bool:
    if isinstance(obj, dict):
        t = obj.get("type")
        if t in ("tool_use", "tool-call", "tool_call", "function_call"):
            return True
        if "tool_use_id" in obj or "tool_name" in obj:
            return True
        for v in obj.values():
            if looks_like_tool_use(v):
                return True
        return False
    if isinstance(obj, list):
        return any(looks_like_tool_use(v) for v in obj)
    return False


def looks_like_tool_result(obj: Any) -> bool:
    if isinstance(obj, dict):
        t = obj.get("type")
        if t in ("tool_result", "tool-output", "tool_output", "function_result"):
            return True
        if "tool_use_id" in obj and ("content" in obj or "output" in obj):
            return True
        for v in obj.values():
            if looks_like_tool_result(v):
                return True
        return False
    if isinstance(obj, list):
        return any(looks_like_tool_result(v) for v in obj)
    return False


def read_tail_lines(path: Path, max_lines: int = 120, max_bytes: int = 512 * 1024) -> list[str]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    take = min(size, max_bytes)
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - take))
            blob = f.read()
    except OSError:
        return []
    text = blob.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-max_lines:]


@dataclass
class SourceSnapshot:
    name: str
    active_ts: float
    mode: str          # idle | busy | attention | sleep
    detail: str


class LogSource:
    def __init__(self, name: str, pattern: str) -> None:
        self.name = name
        self.pattern = os.path.expanduser(pattern)

    def snapshot(self) -> Optional[SourceSnapshot]:
        files = glob.glob(self.pattern, recursive=True)
        if not files:
            return None
        newest = max((Path(p) for p in files), key=lambda p: p.stat().st_mtime)
        lines = read_tail_lines(newest)
        if not lines:
            return None

        last_ts: Optional[float] = None
        mode = "idle"
        detail = f"{self.name}: idle"

        for line in reversed(lines):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_ts = (
                iso_to_ts(obj.get("timestamp"))
                or iso_to_ts(obj.get("created_at"))
                or newest.stat().st_mtime
            )
            role = obj.get("role") or obj.get("sender") or obj.get("author")
            if isinstance(role, dict):
                role = role.get("role")
            role = str(role).lower() if role is not None else ""

            if looks_like_tool_use(obj) and role in ("assistant", "model", "agent", ""):
                age = now_ts() - event_ts
                mode = "attention" if age > STALE_SECS else "busy"
                detail = f"{self.name}: tool_use"
                last_ts = event_ts
                break
            if looks_like_tool_result(obj) and role in ("user", "tool", "system", ""):
                mode = "busy"
                detail = f"{self.name}: tool_result"
                last_ts = event_ts
                break
            if role in ("assistant", "model", "agent", "user"):
                mode = "idle"
                detail = f"{self.name}: active"
                last_ts = event_ts
                break

        if last_ts is None:
            last_ts = newest.stat().st_mtime
            mode = "idle"
            detail = f"{self.name}: file-active"

        if now_ts() - last_ts > SLEEP_SECS:
            mode = "sleep"
            detail = f"{self.name}: sleep"

        return SourceSnapshot(self.name, last_ts, mode, detail)


class ClaudeSource(LogSource):
    def __init__(self) -> None:
        super().__init__("claude", "~/.claude/projects/**/*.jsonl")


class CodexSource(LogSource):
    def __init__(self) -> None:
        super().__init__("codex", "~/.codex/sessions/**/*.jsonl")

    def snapshot(self) -> Optional[SourceSnapshot]:
        # Codex 格式不稳定,失败就静默忽略
        try:
            return super().snapshot()
        except Exception:
            return None


# ─────────── 到 Windows agent 的 HTTP 客户端 ───────────


class AgentClient:
    """封装 POST 到 Windows agent(127.0.0.1:47654,SSH RF 过去)。"""

    def __init__(self, host: str, port: int) -> None:
        self._base = f"http://{host}:{port}"
        self._last_state_fail_ts = 0.0
        self._state_fail_warn_interval = 10.0

    def _post(self, path: str, body: dict[str, Any], timeout_s: float) -> Optional[dict[str, Any]]:
        url = f"{self._base}{path}"
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        req = urllib.request.Request(
            url=url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}

    def push_state(self, payload: dict[str, Any]) -> bool:
        """fire-and-forget: 状态推送,隧道断了只 warn 一次不刷屏。"""
        try:
            self._post("/state", payload, STATE_POST_TIMEOUT_S)
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if now_ts() - self._last_state_fail_ts > self._state_fail_warn_interval:
                print(f"[agent] /state unreachable: {exc}  (SSH 隧道 / Windows agent 没起?)")
                self._last_state_fail_ts = now_ts()
            return False
        except Exception as exc:
            print(f"[agent] /state unexpected error: {exc}")
            return False

    def request_prompt(self, req_id: str, tool: str, hint: str, timeout_s: float) -> str:
        """阻塞等硬件 A/B,返回 'once' / 'deny' / 'serial_unavailable' / 'agent_unreachable'。"""
        body = {"id": req_id, "tool": tool, "hint": hint, "timeout": timeout_s}
        try:
            resp = self._post("/prompt", body, max(timeout_s + 5.0, PROMPT_POST_TIMEOUT_S))
        except (urllib.error.URLError, TimeoutError, OSError):
            return "agent_unreachable"
        except Exception:
            return "agent_unreachable"
        if not isinstance(resp, dict):
            return "agent_unreachable"
        if resp.get("error") == "serial_unavailable":
            return "serial_unavailable"
        return str(resp.get("decision", "deny")).strip().lower() or "deny"


# ─────────── 等硬件的 pending 计数(只为了在 state 里报 waiting) ───────────


class PendingCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self._current: Optional[dict[str, str]] = None  # 最新一个 prompt 的 {id,tool,hint}

    def add(self, item: dict[str, str]) -> None:
        with self._lock:
            self._n += 1
            self._current = dict(item)

    def remove(self) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)
            if self._n == 0:
                self._current = None

    def count(self) -> int:
        with self._lock:
            return self._n

    def current(self) -> Optional[dict[str, str]]:
        with self._lock:
            return dict(self._current) if self._current else None


# ─────────── state 构造 + 推送线程 ───────────


class BridgeState:
    def __init__(self, agent: AgentClient, pending: PendingCounter) -> None:
        self.agent = agent
        self.pending = pending
        self.sources = [ClaudeSource(), CodexSource()]
        self.stop_evt = threading.Event()
        self.last_mode = "sleep"

    def best_snapshot(self) -> Optional[SourceSnapshot]:
        snaps: list[SourceSnapshot] = []
        for src in self.sources:
            try:
                snap = src.snapshot()
            except Exception:
                snap = None
            if snap is not None:
                snaps.append(snap)
        if not snaps:
            return None
        snaps.sort(key=lambda s: s.active_ts, reverse=True)
        return snaps[0]

    def build_payload(self) -> dict[str, Any]:
        waiting = self.pending.count()
        snap = self.best_snapshot()
        mode = "sleep"
        msg = "No Claude/Codex activity"
        total = 0
        running = 0
        waiting_n = waiting

        if snap is not None:
            age = now_ts() - snap.active_ts
            if age > SLEEP_SECS:
                mode = "sleep"
                msg = f"{snap.name}: sleeping"
            else:
                mode = snap.mode
                msg = snap.detail
                total = 1
                if mode in ("busy", "attention"):
                    running = 1
        if waiting > 0:
            mode = "attention"
            total = max(total, 1)
            running = 0
            waiting_n = waiting
            msg = f"awaiting approval ({waiting})"

        self.last_mode = mode
        payload: dict[str, Any] = {
            "total": total,
            "running": running,
            "waiting": waiting_n,
            "msg": msg[:23],
            "entries": [msg[:88]],
        }
        cur = self.pending.current()
        if cur is not None:
            payload["prompt"] = cur
        return payload


def state_writer(state: BridgeState) -> None:
    while not state.stop_evt.is_set():
        payload = state.build_payload()
        state.agent.push_state(payload)
        time.sleep(STATE_POLL_SECS)


# ─────────── 给 hook 的 HTTP server(和原项目 /permission 端点兼容) ───────────


def make_handler(state: BridgeState, agent: AgentClient, pending: PendingCounter, default_timeout_s: float):
    class Handler(BaseHTTPRequestHandler):
        server_version = "serial-bridge-linux/1.0"

        def _send_json(self, code: int, obj: dict[str, Any]) -> None:
            body = (json.dumps(obj, ensure_ascii=True) + "\n").encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send_json(200, {
                    "ok": True,
                    "pending": pending.count(),
                    "mode": state.last_mode,
                })
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/permission":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._send_json(400, {"ok": False, "error": "empty_body"})
                return
            try:
                raw = self.rfile.read(length)
                req = json.loads(raw.decode("utf-8"))
            except Exception:
                self._send_json(400, {"ok": False, "error": "bad_json"})
                return

            req_id = str(req.get("id", "")).strip()
            if not req_id:
                self._send_json(400, {"ok": False, "error": "missing_id"})
                return
            tool = str(req.get("tool", "Tool"))[:20]
            hint = str(req.get("hint", ""))[:43]
            wait_s = float(req.get("timeout", default_timeout_s))
            wait_s = max(1.0, min(wait_s, 300.0))

            pending.add({"id": req_id, "tool": tool, "hint": hint})
            try:
                decision = agent.request_prompt(req_id, tool, hint, wait_s)
            finally:
                pending.remove()

            if decision == "serial_unavailable":
                self._send_json(503, {
                    "ok": False, "id": req_id, "decision": "deny",
                    "error": "serial_unavailable",
                })
                return
            if decision == "agent_unreachable":
                # Windows agent 连不上(SSH 隧道断了或 agent 没起):
                # 给 hook 返 503,hook_permission.py 会回落到原生审批
                self._send_json(503, {
                    "ok": False, "id": req_id, "decision": "deny",
                    "error": "agent_unreachable",
                })
                return
            self._send_json(200, {"ok": True, "id": req_id, "decision": decision})

        def log_message(self, format: str, *args: Any) -> None:
            _ = (format, args)

    return Handler


def run_http(
    state: BridgeState, agent: AgentClient, pending: PendingCounter,
    host: str, port: int, default_timeout_s: float,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(state, agent, pending, default_timeout_s))
    t = threading.Thread(target=server.serve_forever, name="http-server", daemon=True)
    t.start()
    print(f"[http] listening on http://{host}:{port}  (for hook_permission.py)")
    return server


# ─────────── 入口 ───────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Claude Buddy Serial — Linux 侧桥")
    p.add_argument("--host", default=DEFAULT_HTTP_HOST,
                   help="本地 HTTP 监听地址,给 hook 用 (default: 127.0.0.1)")
    p.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT,
                   help="本地 HTTP 端口,hook_permission.py 默认就是这个 (default: 19191)")
    p.add_argument("--agent-host", default=DEFAULT_AGENT_HOST,
                   help="Windows agent 地址 (通过 SSH RF 映射到本地 127.0.0.1)")
    p.add_argument("--agent-port", type=int, default=DEFAULT_AGENT_PORT,
                   help="Windows agent 端口 (default: 47654,需和 agent 端启动参数一致)")
    p.add_argument("--permission-timeout", type=float, default=60.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    agent = AgentClient(args.agent_host, args.agent_port)
    pending = PendingCounter()
    state = BridgeState(agent, pending)

    threading.Thread(target=state_writer, args=(state,),
                     name="state-writer", daemon=True).start()
    server = run_http(state, agent, pending, args.host, args.http_port, args.permission_timeout)

    print(f"[bridge] posting state to http://{args.agent_host}:{args.agent_port}/state every {STATE_POLL_SECS}s")
    print("[bridge] 确认 SSH 配置里有  RemoteForward 47654 127.0.0.1:47654")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_evt.set()
        try:
            server.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
