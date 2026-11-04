import os
import signal
import subprocess
import sys
import threading
import time
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
            # Spawn overlay UI subprocess with Wayland bypass hint enabled
            env = os.environ.copy()
            env["QT_WAYLAND_USE_BYPASSWINDOWMANAGERHINT"] = "1"
            OVERLAY_PROCESS = subprocess.Popen([sys.executable, "-m", "agentctl.ui.overlay"], env=env)
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


def run_hotkey_listener():
    """Listens for global shortcut (default Super+Space via evdev/pynput)."""
    print("[Hotkey] Global hotkey listener active (toggle: Super+Space)")
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
