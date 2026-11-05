#!/usr/bin/env python3
"""AgentCTL Real-Time Live Diagnostics & Telemetry Monitor.

Features:
    1. Status Header (Section 7): Compact live status block at the top.
       Displays daemon, connections, session, FSM, orb, mic, tts, LLM backend,
       component heartbeats, and model budgets.
    2. Event Log Panel (Section 8 & 9): Live scrolling event logs with level filtering.
    3. Interactive Controls:
       - 'd': toggle between INFO and DEBUG log level on daemon without restarting
       - 'c': clear screen / logs
       - 'q': quit monitor
"""

import json
import os
import select
import socket
import sys
import termios
import threading
import time
import tty
from pathlib import Path
from typing import Optional

from agentctl.cli import format_duration, format_status_header, get_log_dir, query_daemon_status
from agentctl.client.daemon import get_socket_path

CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"]) if hasattr(os, "sysconf") else 100


class RawTerminal:
    """Context manager to enable non-blocking single-key reads in Linux terminal."""

    def __init__(self):
        self.old_settings = None

    def __enter__(self):
        if sys.stdin.isatty():
            try:
                self.old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                self.old_settings = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass


def read_key_nonblocking() -> Optional[str]:
    """Reads a single key if available on stdin without blocking."""
    if not sys.stdin.isatty():
        return None
    r, _, _ = select.select([sys.stdin], [], [], 0.0)
    if r:
        try:
            return sys.stdin.read(1)
        except Exception:
            return None
    return None


def send_set_log_level(level: str) -> bool:
    """Sends loglevel change to daemon via UDS HTTP endpoint."""
    sock_path = get_socket_path()
    if not sock_path.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(str(sock_path))
        body = json.dumps({"level": level}).encode("utf-8")
        req = (
            f"POST /loglevel HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8") + body
        s.sendall(req)
        s.recv(1024)
        s.close()
        return True
    except Exception:
        return False


def run_monitor():
    log_dir = get_log_dir()
    daemon_log_path = log_dir / "daemon.log"

    current_log_level = "info"
    max_log_lines = 15
    recent_logs = []

    last_header_update = 0.0
    cached_header = ""

    # Open daemon.log for reading if exists
    log_file = None
    if daemon_log_path.exists():
        try:
            log_file = open(daemon_log_path, "r", encoding="utf-8", errors="replace")
            # Seek near end to show recent lines
            log_file.seek(0, os.SEEK_END)
            size = log_file.tell()
            seek_pos = max(0, size - 4096)
            log_file.seek(seek_pos)
            for line in log_file:
                line = line.rstrip()
                if line:
                    recent_logs.append(line)
            recent_logs = recent_logs[-max_log_lines:]
        except Exception:
            log_file = None

    with RawTerminal():
        # Clear screen once
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

        try:
            while True:
                now = time.time()

                # 1. Update status header every 500ms
                if now - last_header_update >= 0.5:
                    code, details = query_daemon_status(timeout_s=0.4)
                    if details:
                        cached_header = format_status_header(details)
                    else:
                        cached_header = (
                            "=" * 80 + "\n"
                            "\033[31m\033[1mAGENTCTL STATUS: UNKNOWN (daemon not reachable)\033[0m\n"
                            f"Daemon socket {get_socket_path()} is offline or starting up...\n"
                            + "=" * 80
                        )
                    last_header_update = now

                    # Redraw entire screen: Header + Logs + Help bar
                    sys.stdout.write("\033[H")  # Move to top-left
                    sys.stdout.write(cached_header + "\n\n")

                    # Log panel header
                    sys.stdout.write(
                        f"\033[1m--- LIVE EVENT LOG (Filter: \033[36m{current_log_level.upper()}\033[0m\033[1m) ---\033[0m\n"
                    )
                    for log_line in recent_logs[-max_log_lines:]:
                        # Simple level coloring
                        colored = log_line
                        if "ERROR" in log_line:
                            colored = f"\033[31m{log_line}\033[0m"
                        elif "WARN" in log_line:
                            colored = f"\033[33m{log_line}\033[0m"
                        elif "DEBUG" in log_line:
                            colored = f"\033[90m{log_line}\033[0m"
                        # Clear to end of line
                        sys.stdout.write(f"\033[2K{colored}\n")

                    # Fill remaining space
                    padding = max_log_lines - len(recent_logs[-max_log_lines:])
                    for _ in range(padding):
                        sys.stdout.write("\033[2K\n")

                    sys.stdout.write("\033[2K\n")
                    sys.stdout.write(
                        "\033[7m [d] Toggle DEBUG/INFO | [c] Clear Logs | [q] Quit \033[0m\n"
                    )
                    sys.stdout.flush()

                # 2. Check for new log lines from file
                if log_file is None and daemon_log_path.exists():
                    try:
                        log_file = open(daemon_log_path, "r", encoding="utf-8", errors="replace")
                        log_file.seek(0, os.SEEK_END)
                    except Exception:
                        log_file = None

                if log_file:
                    try:
                        line = log_file.readline()
                        if line:
                            line = line.rstrip()
                            if line:
                                recent_logs.append(line)
                                if len(recent_logs) > max_log_lines * 2:
                                    recent_logs = recent_logs[-max_log_lines:]
                    except Exception:
                        pass

                # 3. Read keypress
                key = read_key_nonblocking()
                if key:
                    if key.lower() == "q":
                        break
                    elif key.lower() == "c":
                        recent_logs.clear()
                        sys.stdout.write("\033[2J\033[H")
                        sys.stdout.flush()
                    elif key.lower() == "d":
                        current_log_level = "debug" if current_log_level == "info" else "info"
                        send_set_log_level(current_log_level)
                        recent_logs.append(f"[Monitor] Switched daemon log level to {current_log_level.upper()}")

                time.sleep(0.05)

        except KeyboardInterrupt:
            pass
        finally:
            if log_file:
                log_file.close()
            sys.stdout.write("\n\033[0m[Monitor] Exited cleanly.\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_monitor()
