#!/usr/bin/env python3
"""AgentCTL CLI - Status command, log viewer, and orchestrator control.

Usage:
    agentctl status [--json]
    agentctl logs [--follow] [--level LEVEL] [--component COMP] [--turn TURN] [--since SINCE] [--json]
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agentctl.client.daemon import get_socket_path
from agentctl.config import REPO_ROOT


def get_log_dir() -> Path:
    custom = os.environ.get("AGENTCTL_LOG_DIR")
    if custom and custom.strip():
        p = Path(os.path.expanduser(custom.strip()))
        return p

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(f"/home/{sudo_user}/.local/state/agentctl/logs")

    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "agentctl" / "logs"

    return Path.home() / ".local" / "state" / "agentctl" / "logs"


def get_state_dir() -> Path:
    custom = os.environ.get("AGENTCTL_STATE_DIR")
    if custom and custom.strip():
        return Path(os.path.expanduser(custom.strip()))

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(f"/home/{sudo_user}/.local/state/agentctl")

    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "agentctl"

    return Path.home() / ".local" / "state" / "agentctl"


def query_daemon_status(timeout_s: float = 1.0) -> tuple[int, Optional[dict]]:
    """Queries the daemon via UDS socket.
    Returns:
        (exit_code, status_dict)
        Exit codes:
          0 = running and healthy
          1 = degraded
          3 = not running / not reachable
          4 = error / cannot tell
    """
    sock_path = get_socket_path()
    if not sock_path.exists():
        return 3, None

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout_s)
    try:
        s.connect(str(sock_path))
        # Send simple HTTP GET request
        req = b"GET /status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        s.sendall(req)

        resp_bytes = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp_bytes.extend(chunk)
        s.close()
    except (socket.timeout, ConnectionRefusedError, FileNotFoundError, OSError):
        return 3, None
    except Exception:
        return 4, None

    # Parse HTTP response
    try:
        header_end = resp_bytes.find(b"\r\n\r\n")
        if header_end == -1:
            return 4, None
        body = resp_bytes[header_end + 4 :]
        data = json.loads(body.decode("utf-8"))

        # Unwrap ApiResponse envelope
        if isinstance(data, dict):
            details = None
            if "data" in data and isinstance(data["data"], dict):
                inner = data["data"]
                details = inner.get("details") or inner
            elif "details" in data:
                details = data["details"]
            elif "daemon" in data:
                details = data

            if not details:
                return 4, None

            daemon_info = details.get("daemon", {})
            st = daemon_info.get("status", "unknown").lower()
            if st == "running":
                return 0, details
            elif st == "degraded":
                return 1, details
            elif st in ("stopping", "stopped", "down"):
                return 3, details
            else:
                return 4, details
        return 4, None
    except Exception:
        return 4, None


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}m {secs}s"
    hours = int(mins // 60)
    mins = int(mins % 60)
    return f"{hours}h {mins}m"


def format_status_header(details: dict) -> str:
    daemon = details.get("daemon", {})
    session = details.get("session", {})
    fsm = details.get("fsm", {})
    orb = details.get("orb", {})
    mic = details.get("mic", {})
    tts = details.get("tts", {})
    llm = details.get("llm", {})
    conns = details.get("connections", [])
    comps = details.get("components", {})
    models = details.get("models", {})

    status_str = daemon.get("status", "UNKNOWN").upper()
    pid = daemon.get("pid", 0)
    uptime_s = daemon.get("uptime_secs", 0)
    version = daemon.get("version", "0.1.0")
    git_hash = daemon.get("git_hash", "dev")
    config_path = daemon.get("config_path", "")
    schema_ver = daemon.get("telemetry_schema_version", "1.0.0")

    color = "\033[32m" if status_str == "RUNNING" else ("\033[33m" if status_str == "DEGRADED" else "\033[31m")
    reset = "\033[0m"
    bold = "\033[1m"

    lines = [
        "=" * 80,
        f"{bold}AGENTCTL STATUS:{reset} {color}{bold}{status_str}{reset} "
        f"(PID: {pid} | Uptime: {format_duration(uptime_s)} | v{version} ({git_hash}) | Schema: {schema_ver})",
        f"Config: {config_path or '<default>'}",
        "-" * 80,
    ]

    # FSM
    fsm_state = fsm.get("current_state", "idle").upper()
    fsm_time = format_duration(fsm.get("time_in_state_secs", 0))
    prev = fsm.get("previous_state") or "none"
    reason = fsm.get("transition_reason") or "none"
    lines.append(f"{bold}FSM:{reset}        {fsm_state} (for {fsm_time}, prev: {prev}, reason: {reason})")

    # Session
    sid = session.get("session_id") or "none"
    turn = session.get("turn_number", 0)
    start_t = session.get("time_since_start_secs")
    start_str = format_duration(start_t) + " ago" if start_t is not None else "not started"
    exit_p = "YES" if session.get("exit_phrase_pending") else "NO"
    lines.append(f"{bold}Session:{reset}    {sid} | Turn: {turn} | Started: {start_str} | Exit Pending: {exit_p}")

    # Orb / UI
    orb_st = orb.get("orb_state", "idle").upper()
    glow = "ON" if orb.get("edge_glow_enabled") else "OFF"
    lvl = orb.get("last_level", 0.0)
    lines.append(f"{bold}Orb / UI:{reset}   {orb_st} | Edge Glow: {glow} | Level: {lvl:.3f}")

    # Mic & TTS
    mic_st = mic.get("state", "armed").upper()
    mic_r = mic.get("reason", "ready")
    tts_st = tts.get("state", "idle").upper()
    tts_sent = tts.get("current_sentence", 0)
    lines.append(f"{bold}Mic / TTS:{reset}  Mic: {mic_st} ({mic_r}) | TTS: {tts_st} (sentence: {tts_sent})")

    # LLM
    llm_st = llm.get("status", "ready").upper()
    model = llm.get("model_name", "gpt-4o-mini")
    last_res = llm.get("last_request_result") or "none"
    lat = llm.get("last_latency_ms")
    lat_str = f"{lat}ms" if lat is not None else "none"
    lines.append(f"{bold}LLM Backend:{reset} {llm_st} (model: {model} | last: {last_res} in {lat_str})")

    # Clients
    if conns:
        conn_strs = [f"{c.get('role', 'unknown')} ({format_duration(c.get('connected_duration_secs', 0))})" for c in conns]
        lines.append(f"{bold}Clients:{reset}    {' | '.join(conn_strs)}")
    else:
        lines.append(f"{bold}Clients:{reset}    none connected")

    # Components
    comp_strs = []
    for name, c in sorted(comps.items()):
        c_st = c.get("status", "unknown").upper()
        age = format_duration(c.get("last_heartbeat_age_secs", 0))
        c_col = "\033[32m" if c_st == "UP" else ("\033[31m" if c_st == "DOWN" else "\033[33m")
        comp_strs.append(f"{name}: {c_col}{c_st}{reset} ({age})")
    lines.append(f"{bold}Components:{reset} {' | '.join(comp_strs) if comp_strs else 'none'}")

    # Models & Memory
    mod_list = ", ".join(models.get("models_loaded", []))
    ram = models.get("ram_mb", 0.0)
    lines.append(f"{bold}Models:{reset}     {mod_list} | RAM: {ram:.1f} MB")

    # Last error
    if daemon.get("last_error"):
        err = daemon["last_error"]
        lines.append(f"\033[31mLast Error: [{err.get('time')}] {err.get('message')}\033[0m")

    lines.append("=" * 80)
    return "\n".join(lines)


def parse_since(since_str: str) -> float:
    """Parses duration string like 10m, 1h, 30s into seconds."""
    match = re.match(r"^(\d+)([smhd])$", since_str.strip().lower())
    if not match:
        raise ValueError(f"Invalid duration format: '{since_str}'. Expected e.g. 30s, 10m, 2h")
    val, unit = int(match.group(1)), match.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return float(val * mult)


def cmd_status(args):
    code, details = query_daemon_status()
    if args.json:
        if details:
            print(json.dumps(details, indent=2))
        else:
            print(json.dumps({"status": "stopped", "error": "Daemon not reachable", "code": code}, indent=2))
        sys.exit(code)

    if details:
        print(format_status_header(details))
        sys.exit(code)
    else:
        print("=" * 80)
        print("\033[31m\033[1mAGENTCTL STATUS: UNKNOWN (daemon not reachable)\033[0m")
        print("=" * 80)
        print(f"Daemon socket {get_socket_path()} is not answering.")
        sys.exit(3)


def cmd_logs(args):
    log_dir = get_log_dir()
    target_file = log_dir / ("events.jsonl" if args.json else "daemon.log")

    if not target_file.exists():
        print(f"[agentctl logs] Log file not found: {target_file}", file=sys.stderr)
        sys.exit(0)

    now = time.time()
    min_time_s = now - parse_since(args.since) if args.since else 0.0

    levels_order = {"debug": 0, "info": 1, "warn": 2, "error": 3}
    min_level_val = levels_order.get((args.level or "info").lower(), 1)

    def matches_filter(line: str) -> tuple[bool, str]:
        line = line.rstrip()
        if not line:
            return False, ""

        if args.json:
            try:
                ev = json.loads(line)
            except Exception:
                return False, ""

            # Level filter
            lvl = ev.get("level", "info").lower()
            if levels_order.get(lvl, 1) < min_level_val:
                return False, ""

            # Component filter
            if args.component and ev.get("component", "").lower() != args.component.lower():
                return False, ""

            # Turn filter
            if args.turn is not None and ev.get("turn_id") != args.turn:
                return False, ""

            # Since filter
            if min_time_s > 0:
                ts = ev.get("timestamp")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if dt.timestamp() < min_time_s:
                            return False, ""
                    except Exception:
                        pass

            return True, line
        else:
            # Line format: 2026-11-05 16:26:03.412 INFO  session  ...
            parts = line.split(maxsplit=3)
            if len(parts) >= 3:
                lvl = parts[2].strip().lower()
                if levels_order.get(lvl, 1) < min_level_val:
                    return False, ""

            if args.component:
                if len(parts) >= 4:
                    comp = parts[3].split()[0].lower() if parts[3].split() else ""
                    if comp != args.component.lower():
                        return False, ""

            if args.turn is not None:
                if f"turn={args.turn}" not in line:
                    return False, ""

            return True, line

    # Print matching lines from file
    with open(target_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ok, text = matches_filter(line)
            if ok:
                print(text)

        if args.follow:
            try:
                while True:
                    line = f.readline()
                    if line:
                        ok, text = matches_filter(line)
                        if ok:
                            print(text, flush=True)
                    else:
                        time.sleep(0.1)
            except KeyboardInterrupt:
                pass


def cmd_local_llm(args):
    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file = state_dir / "llama-server.pid"
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "llama-server.log"

    action = getattr(args, "action", "status")

    if action == "start":
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                print(f"llama-server is already running with PID {pid}")
                return
            except (ValueError, OSError):
                pid_file.unlink(missing_ok=True)

        model_path = getattr(args, "model", None)
        if not model_path:
            default_model = Path.home() / ".local/share/agentctl/models/llm/Qwen3.5-9B-Q4_K_M.gguf"
            if default_model.exists():
                model_path = str(default_model)
            else:
                llm_dir = Path.home() / ".local/share/agentctl/models/llm"
                ggufs = list(llm_dir.glob("*.gguf"))
                if ggufs:
                    model_path = str(ggufs[0])
                else:
                    print(f"Error: No model found at {default_model}. Please specify --model.", file=sys.stderr)
                    sys.exit(1)

        port = args.port
        ngl = args.ngl
        ctx = args.ctx
        threads = args.threads

        server_bin = Path.home() / ".local/bin/llama-server"
        if not server_bin.exists():
            server_bin = Path.home() / ".local/share/agentctl/llama.cpp/llama-server"
        if not server_bin.exists():
            print(f"Error: llama-server binary not found at {server_bin}", file=sys.stderr)
            sys.exit(1)

        cmd = [
            str(server_bin),
            "-m", model_path,
            "--host", "127.0.0.1",
            "--port", str(port),
            "-ngl", str(ngl),
            "-c", str(ctx),
            "--flash-attn", "on",
            "--cache-type-k", "q8_0",
            "--reasoning", "off",
            "-t", str(threads),
        ]

        print(f"Starting llama-server (model: {Path(model_path).name}, port: {port}, ngl: {ngl}, ctx: {ctx})...")
        with open(log_file, "a", encoding="utf-8") as out:
            proc = subprocess.Popen(
                cmd,
                stdout=out,
                stderr=out,
                preexec_fn=os.setsid,
            )
            pid_file.write_text(str(proc.pid))

        print(f"llama-server process spawned with PID {proc.pid}. Waiting for readiness...")
        ready = False
        for _ in range(25):
            time.sleep(0.2)
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                pass

        if ready:
            print(f"llama-server is READY on http://127.0.0.1:{port}")
        else:
            print(f"llama-server is loading weights in background (PID {proc.pid}). Check logs with:")
            print(f"  agentctl local-llm logs  (or tail -f {log_file})")

    elif action == "stop":
        if not pid_file.exists():
            print("llama-server is not running (no PID file).")
            return
        try:
            pid = int(pid_file.read_text().strip())
            print(f"Stopping llama-server (PID {pid})...")
            os.kill(pid, 15)
            for _ in range(20):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
            else:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
            pid_file.unlink(missing_ok=True)
            print("llama-server stopped.")
        except (ValueError, OSError) as e:
            print(f"Error stopping process: {e}")
            pid_file.unlink(missing_ok=True)

    elif action == "status":
        running = False
        pid = None
        if pid_file.exists():
            try:
                p = int(pid_file.read_text().strip())
                os.kill(p, 0)
                running = True
                pid = p
            except (ValueError, OSError):
                pid_file.unlink(missing_ok=True)

        if not running:
            print("llama-server: STOPPED")
            return

        print(f"llama-server: RUNNING (PID: {pid})")
        try:
            req = urllib.request.Request("http://127.0.0.1:8080/health")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                print(f"Health Status: HTTP {resp.status} (Ready for inference)")
        except Exception as e:
            print(f"Health Status: Loading or unready ({e})")

        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                parts = res.stdout.strip().split(",")
                if len(parts) >= 2:
                    print(f"GPU VRAM Usage: {parts[0].strip()} MiB / {parts[1].strip()} MiB")
        except Exception:
            pass

    elif action == "logs":
        if log_file.exists():
            subprocess.run(["tail", "-n", "50", "-f" if getattr(args, "follow", False) else "-n50", str(log_file)])
        else:
            print(f"No log file found at {log_file}")


def main():
    parser = argparse.ArgumentParser(prog="agentctl", description="AgentCTL Linux Orchestrator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Query daemon status header and component health")
    p_status.add_argument("--json", action="store_true", help="Print status snapshot in JSON format")

    # logs
    p_logs = subparsers.add_parser("logs", help="View and filter persistent event logs")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow log output in real-time")
    p_logs.add_argument("--level", choices=["debug", "info", "warn", "error"], help="Minimum log level")
    p_logs.add_argument("--component", help="Filter by component name (e.g. stt, tts, session, fsm)")
    p_logs.add_argument("--turn", type=int, help="Filter by turn ID")
    p_logs.add_argument("--since", help="Show logs newer than duration (e.g. 30s, 10m, 2h)")
    p_logs.add_argument("--json", action="store_true", help="Output raw JSON lines from events.jsonl")

    # local-llm
    p_llm = subparsers.add_parser("local-llm", help="Manage local llama-server LLM process")
    llm_sub = p_llm.add_subparsers(dest="action", required=True)

    p_llm_start = llm_sub.add_parser("start", help="Start local llama-server in background")
    p_llm_start.add_argument("--model", type=str, help="Path to GGUF model file")
    p_llm_start.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    p_llm_start.add_argument("-ngl", type=int, default=99, help="Number of GPU layers to offload (default: 99)")
    p_llm_start.add_argument("-c", "--ctx", type=int, default=4096, help="Context size (default: 4096)")
    p_llm_start.add_argument("-t", "--threads", type=int, default=4, help="CPU threads (default: 4)")

    llm_sub.add_parser("stop", help="Stop running llama-server")
    llm_sub.add_parser("status", help="Check llama-server health and VRAM usage")

    p_llm_logs = llm_sub.add_parser("logs", help="View llama-server log output")
    p_llm_logs.add_argument("-f", "--follow", action="store_true", help="Follow log output")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "local-llm":
        cmd_local_llm(args)


if __name__ == "__main__":
    main()

