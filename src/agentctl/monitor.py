#!/usr/bin/env python3
"""AgentCTL Real-Time Live Diagnostics Monitor.

Usage:
    .venv/bin/python -m agentctl.monitor

Monitors:
    1. Overlay process PID, CPU % (sampled every 500ms), and Resident RAM
    2. Microphone input volume & VAD status in real-time
    3. Whisper trigger events, latencies, and predicted transcripts
    4. Daemon state transitions
"""

import os
import sys
import time
import subprocess
from pathlib import Path

LOG_FILE = Path.home() / ".local" / "share" / "agentctl" / "overlay.log"
CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])


def get_process_cpu_and_mem(pid: int) -> tuple[float, float]:
    """Reads /proc/<pid>/stat to calculate CPU percentage and RSS RAM (MB)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            rss_mb = 0.0
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_mb = float(line.split()[1]) / 1024.0
                    break

        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().split()
            ticks = int(fields[13]) + int(fields[14])
        return ticks, rss_mb
    except Exception:
        return 0, 0.0


def find_overlay_pid() -> int | None:
    try:
        out = subprocess.run(["pgrep", "-f", "agentctl.ui.overlay"], capture_output=True, text=True)
        pids = [int(p) for p in out.stdout.split() if p.strip() and int(p) != os.getpid()]
        return pids[0] if pids else None
    except Exception:
        return None


def run_monitor():
    total_cores = os.cpu_count() or 1
    print("\033[2J\033[H", end="")  # Clear terminal
    print("=" * 72)
    print("           AgentCTL Live Diagnostics & STT Resource Monitor             ")
    print(f"               (System: {total_cores} CPU cores | Log: {LOG_FILE})")
    print("=" * 72)
    print(" Instructions:")
    print("  - Keep this terminal open while testing.")
    print("  - In another terminal or via hotkey (Super+Space), toggle the overlay.")
    print("  - Watch the live telemetry, RMS audio volume, and Whisper triggers below.")
    print("  - Press Ctrl+C at any time to exit.")
    print("=" * 72 + "\n")

    # Ensure log file exists
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        # Seek to end of existing logs so we only show fresh events
        f.seek(0, os.SEEK_END)

        last_cpu_time = time.time()
        prev_pid = None
        prev_ticks = 0

        while True:
            # Check overlay process
            pid = find_overlay_pid()
            now = time.time()

            # Every 500ms, compute CPU if overlay is active
            if now - last_cpu_time >= 0.5:
                if pid:
                    ticks, rss = get_process_cpu_and_mem(pid)
                    if pid == prev_pid and prev_ticks > 0:
                        wall = now - last_cpu_time
                        cpu_sec = (ticks - prev_ticks) / CLK_TCK
                        single_core_pct = (cpu_sec / wall) * 100.0
                        sys_pct = single_core_pct / total_cores
                        status_line = (
                            f"\033[36m[PROC MONITOR]\033[0m Overlay PID: {pid} | "
                            f"Total System CPU: \033[1m{sys_pct:4.1f}%\033[0m ({single_core_pct:5.1f}% 1-core) | "
                            f"RAM: {rss:4.1f} MB"
                        )
                        print(status_line, flush=True)
                    prev_pid = pid
                    prev_ticks = ticks
                else:
                    if prev_pid is not None:
                        print("\033[33m[PROC MONITOR] Overlay closed.\033[0m", flush=True)
                        prev_pid = None
                        prev_ticks = 0
                last_cpu_time = now

            # Read any new log lines
            line = f.readline()
            if line:
                print(line.rstrip(), flush=True)
            else:
                time.sleep(0.05)


if __name__ == "__main__":
    try:
        run_monitor()
    except KeyboardInterrupt:
        print("\n[Monitor] Stopped.")

