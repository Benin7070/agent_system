"""Screen and monitor detection utilities for multi-monitor setups."""

import sys
from typing import Dict, List, Optional
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QCursor, QGuiApplication, QScreen
from PyQt6.QtWidgets import QApplication


def get_active_screen() -> Optional[QScreen]:
    """Identifies the target display monitor.
    If AGENTCTL_SCREEN is configured, selects by screen index or name.
    Otherwise automatically selects the monitor where the user's mouse cursor is located,
    falling back to the primary screen."""
    from agentctl.config import TARGET_SCREEN
    screens = QGuiApplication.screens()
    if TARGET_SCREEN:
        if TARGET_SCREEN.isdigit():
            idx = int(TARGET_SCREEN)
            if 0 <= idx < len(screens):
                return screens[idx]
        for s in screens:
            if s.name().lower() == TARGET_SCREEN.lower():
                return s

    cursor_pos = QCursor.pos()
    active = QGuiApplication.screenAt(cursor_pos)
    if active is not None:
        return active
    return QGuiApplication.primaryScreen()


def list_screens_info() -> List[Dict]:
    """Returns structured information about all connected displays."""
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    cursor_pos = QCursor.pos()
    active = get_active_screen()

    result = []
    for idx, screen in enumerate(screens):
        geo: QRect = screen.geometry()
        avail: QRect = screen.availableGeometry()
        is_primary = (screen == primary)
        is_active = (screen == active)
        result.append({
            "index": idx,
            "name": screen.name(),
            "geometry": {
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height(),
            },
            "available_geometry": {
                "x": avail.x(),
                "y": avail.y(),
                "width": avail.width(),
                "height": avail.height(),
            },
            "device_pixel_ratio": screen.devicePixelRatio(),
            "refresh_rate": round(screen.refreshRate(), 1),
            "is_primary": is_primary,
            "is_active": is_active,
        })
    return result


def print_screens_cli():
    """CLI printer for inspectable display configuration."""
    cursor_pos = QCursor.pos()
    screens_data = list_screens_info()

    print("=" * 68)
    print(" Display Configuration & Active Screen Inspector")
    print("=" * 68)
    print(f"Mouse Cursor Position: ({cursor_pos.x()}, {cursor_pos.y()})")
    print("-" * 68)

    for s in screens_data:
        idx = s["index"]
        name = s["name"]
        g = s["geometry"]
        ag = s["available_geometry"]
        hz = s["refresh_rate"]
        dpr = s["device_pixel_ratio"]
        tags = []
        if s["is_primary"]:
            tags.append("PRIMARY")
        if s["is_active"]:
            tags.append("ACTIVE (Mouse Here)")
        tag_str = f" [{', '.join(tags)}]" if tags else ""

        print(f"Screen {idx}: {name}{tag_str}")
        print(f"  Bounds:      {g['width']}x{g['height']} at x={g['x']}, y={g['y']}")
        print(f"  Work Area:   {ag['width']}x{ag['height']} at x={ag['x']}, y={ag['y']}")
        print(f"  Display:     {hz} Hz, Scale Factor: {dpr}x")
        print()

    active_name = next((s["name"] for s in screens_data if s["is_active"]), "None")
    print(f"--> Target Screen for Voice Assistant Overlay: {active_name}")
    print("=" * 68)


def main():
    # Ensure QApplication exists
    app = QApplication.instance()
    owns_app = False
    if not app:
        app = QApplication(sys.argv)
        owns_app = True

    print_screens_cli()

    if owns_app:
        app.quit()


if __name__ == "__main__":
    main()
