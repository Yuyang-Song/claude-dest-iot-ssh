#!/usr/bin/env python3
"""
Claude Buddy Serial — Windows 侧串口 agent
===========================================

在 Windows 本地跑,负责:
  1) 打开并维护 USB 串口(M5StickC Plus)
  2) 起一个 HTTP server 监听 127.0.0.1:47654,暴露两个接口给 Linux 侧的
     serial_bridge_linux.py 调用:
       - POST /state    body=JSON,直接写一行到串口(设备上用来更新 UI)
       - POST /prompt   body={id,tool,hint,timeout},阻塞等待硬件 A/B 键
                        返回 {"id":..., "decision":"once|deny"}
     以及健康检查:
       - GET  /health

  3) 串口读行:收到 {"cmd":"permission","id","decision"} 时,把对应
     /prompt 请求解阻塞并回复。

跨机链路:
  Linux serial_bridge_linux.py
    ──HTTP──> 127.0.0.1:47654  (Linux 本机)
                    │
                    │  SSH RemoteForward 47654:127.0.0.1:47654
                    ▼
              127.0.0.1:47654  (Windows 本机 = 本文件监听)
                    │
                    │  pyserial
                    ▼
              COM4  ──USB──>  M5StickC Plus

启动:
    python buddy_serial_agent.py                     # 控制台模式,默认 auto 选端口
    python buddy_serial_agent.py --port COM4         # 指定 COM 口
    python buddy_serial_agent.py --http-port 47654   # 改监听端口
    双击 start_buddy_serial.bat                      # 无黑窗后台跑
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import serial
from serial import SerialException
from serial.tools import list_ports

# ─────────── 默认参数 ───────────

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 47654          # 和 claw-jump 的 47653 相邻
DEFAULT_BAUD = 115200
DEFAULT_PORT = "auto"              # 自动探测 COM 口

RECONNECT_SECS = 1.0
AUTO_PORT_LOG_INTERVAL_SECS = 5.0
DEFAULT_PERMISSION_TIMEOUT = 60.0  # 硬件不按 A/B 最多等多久

USB_HINTS = (
    "m5", "m5stick", "cp210", "ch340", "wch",
    "usb serial", "silicon labs", "uart", "esp32",
)
PRIORITY_VIDPID = {
    (0x10C4, 0xEA60),  # CP210x (M5StickC Plus 默认)
    (0x1A86, 0x7523),  # CH340
    (0x303A, 0x1001),  # ESP32-S3 USB-JTAG/serial
}

# pythonw.exe 无 stdout/stderr,打印会 crash。重定向到日志,方便排查闪退
_LOG_FILE = Path(__file__).parent / "buddy_serial_agent.log"
if sys.stdout is None:
    sys.stdout = open(_LOG_FILE, "a", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout


# ─────────── 串口自动选择 ───────────


def choose_auto_port() -> Optional[str]:
    """从系统里找最像 M5Stick 的 COM 口,找不到返回 None。"""
    ports = list(list_ports.comports())
    if not ports:
        return None

    def score(p: Any) -> int:
        text = " ".join(
            str(x).lower()
            for x in (
                getattr(p, "device", ""),
                getattr(p, "name", ""),
                getattr(p, "description", ""),
                getattr(p, "manufacturer", ""),
                getattr(p, "hwid", ""),
            )
        )
        base = 0
        if "bluetooth" in text:
            return -1
        if any(h in text for h in USB_HINTS):
            base += 2
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        if isinstance(vid, int) and isinstance(pid, int):
            if (vid, pid) in PRIORITY_VIDPID:
                base += 6
            else:
                base += 1
        return base

    ranked = sorted(ports, key=score, reverse=True)
    top = ranked[0]
    if score(top) <= 0:
        return None
    return str(top.device)


# ─────────── 串口句柄封装(线程安全) ───────────


class SerialRef:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ser: Optional[serial.Serial] = None

    def get(self) -> Optional[serial.Serial]:
        with self._lock:
            return self._ser

    def replace(self, ser: Optional[serial.Serial]) -> None:
        with self._lock:
            old = self._ser
            self._ser = ser
        if old is not None and old is not ser:
            try:
                try:
                    old.dtr = False
                    old.rts = False
                except Exception:
                    pass
                old.close()
            except Exception:
                pass

    def close_if_same(self, ser: serial.Serial) -> None:
        with self._lock:
            if self._ser is ser:
                self._ser = None
                try:
                    try:
                        ser.dtr = False
                        ser.rts = False
                    except Exception:
                        pass
                    ser.close()
                except Exception:
                    pass

    def write_line(self, obj: dict[str, Any]) -> bool:
        """写一行 JSON + \\n 到串口,失败自动关闭句柄让 connector 重连。"""
        payload = (json.dumps(obj, ensure_ascii=True) + "\n").encode("utf-8")
        ser = self.get()
        if ser is None:
            return False
        try:
            ser.write(payload)
            ser.flush()
            return True
        except SerialException:
            self.close_if_same(ser)
            return False


# ─────────── pending prompts(按 id 匹配硬件回包) ───────────


@dataclass
class PendingPrompt:
    q: queue.Queue  # type: ignore[type-arg]
    created_ts: float


class PromptBroker:
    """HTTP /prompt 收到请求 → 注册 pending → 写串口 → 等串口回执 → 回 HTTP。"""

    def __init__(self, serial_ref: SerialRef) -> None:
        self._serial_ref = serial_ref
        self._lock = threading.Lock()
        self._pending: dict[str, PendingPrompt] = {}

    def request(self, req_id: str, tool: str, hint: str, timeout_s: float) -> str:
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[req_id] = PendingPrompt(q=q, created_ts=time.time())

        wrote = self._serial_ref.write_line(
            {"prompt": {"id": req_id, "tool": tool, "hint": hint}}
        )
        if not wrote:
            with self._lock:
                self._pending.pop(req_id, None)
            return "serial_unavailable"

        decision = "deny"
        try:
            decision = q.get(timeout=timeout_s)
        except queue.Empty:
            decision = "deny"
        finally:
            # 清掉设备屏幕上的 prompt 条
            self._serial_ref.write_line({"prompt": None})
            with self._lock:
                self._pending.pop(req_id, None)
        return decision

    def resolve(self, req_id: str, raw_decision: str) -> bool:
        """串口读线程调用。把硬件返回的 decision 投递给阻塞中的请求。"""
        dec = str(raw_decision).strip().lower()
        mapped = "once" if dec in ("allow", "once", "always") else "deny"
        with self._lock:
            item = self._pending.get(req_id)
        if item is None:
            return False
        try:
            item.q.put_nowait(mapped)
            return True
        except queue.Full:
            return False

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


# ─────────── 串口连接器 / 读线程 ───────────


def serial_connector(
    port_arg: str, baud: int, serial_ref: SerialRef, stop_evt: threading.Event
) -> None:
    """后台线程:只要当前无串口连接就尝试连接,断了就重连。"""
    last_auto_log_ts = 0.0
    while not stop_evt.is_set():
        if serial_ref.get() is not None:
            time.sleep(RECONNECT_SECS)
            continue
        port = port_arg
        if port_arg.lower() == DEFAULT_PORT:
            port = choose_auto_port() or ""
            if not port:
                if time.time() - last_auto_log_ts >= AUTO_PORT_LOG_INTERVAL_SECS:
                    print("[serial] waiting for device (auto port detect)")
                    last_auto_log_ts = time.time()
                time.sleep(RECONNECT_SECS)
                continue
        try:
            ser = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=0.3,
                write_timeout=0.3,
                dsrdtr=False,
                rtscts=False,
            )
            # 避免切换 DTR/RTS 触发板子复位
            try:
                ser.dtr = False
                ser.rts = False
            except Exception:
                pass
            serial_ref.replace(ser)
            print(f"[serial] connected: {port} @ {baud}")
        except SerialException as exc:
            print(f"[serial] connect failed: {exc}")
            time.sleep(RECONNECT_SECS)


def serial_reader(
    serial_ref: SerialRef, broker: PromptBroker, stop_evt: threading.Event
) -> None:
    """后台线程:读串口,遇到 {"cmd":"permission",...} 就投递给 broker。"""
    while not stop_evt.is_set():
        ser = serial_ref.get()
        if ser is None:
            time.sleep(0.2)
            continue
        try:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("{"):
                continue
            obj = json.loads(line)
            if obj.get("cmd") != "permission":
                continue
            req_id = str(obj.get("id", ""))
            dec = str(obj.get("decision", "deny"))
            if not req_id:
                continue
            broker.resolve(req_id, dec)
        except (SerialException, OSError):
            serial_ref.close_if_same(ser)
        except json.JSONDecodeError:
            continue


# ─────────── HTTP server ───────────


def make_handler(serial_ref: SerialRef, broker: PromptBroker, default_timeout_s: float):
    class Handler(BaseHTTPRequestHandler):
        server_version = "buddy-serial-agent/1.0"

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

        def _read_body(self) -> Optional[dict[str, Any]]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            try:
                raw = self.rfile.read(length)
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                return None
            return obj if isinstance(obj, dict) else None

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send_json(200, {
                    "ok": True,
                    "serial_connected": serial_ref.get() is not None,
                    "pending": broker.pending_count(),
                })
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/state":
                body = self._read_body()
                if body is None:
                    self._send_json(400, {"ok": False, "error": "bad_json"})
                    return
                wrote = serial_ref.write_line(body)
                self._send_json(200 if wrote else 503, {
                    "ok": wrote,
                    "serial_connected": serial_ref.get() is not None,
                })
                return

            if self.path == "/prompt":
                body = self._read_body()
                if body is None:
                    self._send_json(400, {"ok": False, "error": "bad_json"})
                    return
                req_id = str(body.get("id", "")).strip()
                if not req_id:
                    self._send_json(400, {"ok": False, "error": "missing_id"})
                    return
                tool = str(body.get("tool", "Tool"))[:20]
                hint = str(body.get("hint", ""))[:43]
                wait_s = float(body.get("timeout", default_timeout_s))
                wait_s = max(1.0, min(wait_s, 300.0))

                decision = broker.request(req_id, tool, hint, wait_s)
                if decision == "serial_unavailable":
                    self._send_json(503, {
                        "ok": False, "id": req_id, "decision": "deny",
                        "error": "serial_unavailable",
                    })
                    return
                self._send_json(200, {"ok": True, "id": req_id, "decision": decision})
                return

            self._send_json(404, {"ok": False, "error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            # 静默 access log;要调试自己取消这行
            _ = (format, args)

    return Handler


def run_http(
    serial_ref: SerialRef,
    broker: PromptBroker,
    host: str,
    port: int,
    default_timeout_s: float,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(serial_ref, broker, default_timeout_s))
    t = threading.Thread(target=server.serve_forever, name="http-server", daemon=True)
    t.start()
    print(f"[http] listening on http://{host}:{port}")
    return server


# ─────────── 入口 ───────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Buddy serial agent (Windows side)")
    p.add_argument("--port", default=DEFAULT_PORT,
                   help="Serial COM port, 'auto' for auto-detect (default: auto)")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    p.add_argument("--host", default=DEFAULT_HTTP_HOST)
    p.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    p.add_argument("--permission-timeout", type=float, default=DEFAULT_PERMISSION_TIMEOUT)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    serial_ref = SerialRef()
    broker = PromptBroker(serial_ref)
    stop_evt = threading.Event()

    threading.Thread(
        target=serial_connector, args=(args.port, args.baud, serial_ref, stop_evt),
        name="serial-connector", daemon=True,
    ).start()
    threading.Thread(
        target=serial_reader, args=(serial_ref, broker, stop_evt),
        name="serial-reader", daemon=True,
    ).start()

    server = run_http(serial_ref, broker, args.host, args.http_port, args.permission_timeout)
    print(f"[buddy-serial-agent] ready. COM={args.port} baud={args.baud}")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        try:
            server.shutdown()
        except Exception:
            pass
        ser = serial_ref.get()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
