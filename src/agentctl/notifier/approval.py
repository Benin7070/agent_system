import subprocess
from typing import Optional
from plyer import notification


def send_approval_notification(
    task_id: str,
    action_summary: str,
    action_hash: str,
) -> bool:
    """Sends a desktop approval notification using notify-send / libnotify."""
    title = f"AgentCTL Approval Request [{task_id}]"
    message = f"{action_summary}\nHash: {action_hash[:16]}..."

    try:
        # Try notify-send with action buttons if available on Linux
        res = subprocess.run(
            [
                "notify-send",
                "-a", "AgentCTL",
                "-u", "critical",
                title,
                message,
            ],
            capture_output=True,
            timeout=2.0,
        )
        return res.returncode == 0
    except Exception:
        # Fallback to plyer notification
        try:
            notification.notify(
                title=title,
                message=message,
                app_name="AgentCTL",
                timeout=10,
            )
            return True
        except Exception:
            return False

