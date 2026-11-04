import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional
from agentctl.client.daemon import DaemonClient

OVERLAY_PROCESS = None


def handle_hotkey_press():
    """Handles toggle logic: activate if IDLE, cancel if ACTIVE."""
    global OVERLAY_PROCESS
    client = DaemonClient()
    try:
        status = client.get_status()
        state = status.get("data", {}).get("state", "IDLE")

        if state == "IDLE":
            print("[Hotkey] State is IDLE -> Activating Overlay")
            # Spawn overlay UI subprocess
            OVERLAY_PROCESS = subprocess.Popen([sys.executable, "-m", "agentctl.ui.overlay"])
        else:
            print(f"[Hotkey] State is {state} -> Cancelling")
            client.cancel()

            # Ensure overlay process is reaped cleanly
            if OVERLAY_PROCESS is not None:
                proc = OVERLAY_PROCESS
                OVERLAY_PROCESS = None

                def reap_worker():
                    time.sleep(0.35)
                    if proc.poll() is None:
                        try:
                            proc.terminate()
                            time.sleep(0.1)
                            if proc.poll() is None:
                                proc.kill()
                        except Exception:
                            pass

                threading.Thread(target=reap_worker, daemon=True).start()
    except Exception as e:
        print(f"[Hotkey] Error communicating with daemon: {e}")


def find_hotkey_device(target_key: int = 149) -> Optional[str]:
    """Finds Linux evdev input device providing target_key (KEY_PROG2 = 149)."""
    dev = os.environ.get("AGENTCTL_HOTKEY_DEVICE")
    if dev and os.path.exists(dev):
        return dev
    if os.path.exists("/proc/bus/input/devices"):
        try:
            with open("/proc/bus/input/devices") as f:
                content = f.read()
            for block in content.strip().split("\n\n"):
                lines = block.splitlines()
                handlers = next((l for l in lines if l.startswith("H: Handlers=")), None)
                keys = next((l for l in lines if l.startswith("B: KEY=")), None)
                if handlers and keys:
                    chunks = keys[len("B: KEY="):].split()
                    words = [int(x, 16) for x in reversed(chunks)]
                    word_idx = target_key // 64
                    bit_idx = target_key % 64
                    if word_idx < len(words) and ((words[word_idx] >> bit_idx) & 1):
                        for part in handlers.split():
                            if part.startswith("event"):
                                p = f"/dev/input/{part}"
                                if os.path.exists(p):
                                    return p
        except Exception:
            pass
    if os.path.exists("/dev/input/event13"):
        return "/dev/input/event13"
    return None


def run_evdev_hotkey_listener(dev_path: str):
    """Listens for hardware KEY_PROG2 (149) events directly from /dev/input/."""
    import struct
    event_size = struct.calcsize("qqHHi")
    print(f"[Hotkey] Listening for KEY_PROG2 (149) on {dev_path}...")
    try:
        with open(dev_path, "rb") as f:
            last_press = 0.0
            while True:
                data = f.read(event_size)
                if not data or len(data) < event_size:
                    break
                sec, usec, ev_type, ev_code, ev_value = struct.unpack("qqHHi", data)
                # EV_KEY = 1, KEY_PROG2 = 149, pressed = 1
                if ev_type == 1 and ev_code == 149 and ev_value == 1:
                    now = time.time()
                    if now - last_press > 0.25:
                        last_press = now
                        print(f"[Hotkey] KEY_PROG2 (149) pressed on {dev_path} -> Toggling")
                        handle_hotkey_press()
    except PermissionError:
        print(f"[Hotkey] Permission denied reading {dev_path}. Run with sudo or add user to 'input' group: sudo usermod -a -G input $USER")
        return False
    except Exception as e:
        print(f"[Hotkey] Error reading {dev_path}: {e}")
        return False
    return True


def run_hotkey_listener():
    """Listens for global shortcut: prefers KEY_PROG2 hardware key, fallback to Super+Space."""
    dev_path = find_hotkey_device(149)
    if dev_path and os.access(dev_path, os.R_OK):
        success = run_evdev_hotkey_listener(dev_path)
        if success:
            return

    print("[Hotkey] Falling back to software global hotkey listener (toggle: Super+Space)")
    try:
        from pynput import keyboard

        current_keys = set()

        def on_press(key):
            try:
                if key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
                    current_keys.add("cmd")
                elif key == keyboard.Key.space:
                    current_keys.add("space")

                if "cmd" in current_keys and "space" in current_keys:
                    handle_hotkey_press()
                    current_keys.clear()
            except Exception:
                pass

        def on_release(key):
            try:
                if key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
                    current_keys.discard("cmd")
                elif key == keyboard.Key.space:
                    current_keys.discard("space")
            except Exception:
                pass

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()
    except ImportError:
        print("[Hotkey] pynput not installed; hotkey listener CLI ready for manual triggers.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "trigger":
        handle_hotkey_press()
    else:
        run_hotkey_listener()
