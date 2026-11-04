#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "    AgentCTL Installer & Setup Script     "
echo "=========================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.cargo/bin"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

# 1. Build release daemon binary
echo "[1/4] Building release binary for agentctl-daemon..."
cd "${SCRIPT_DIR}"
cargo build --release

# 2. Copy binary to ~/.cargo/bin
echo "[2/4] Installing agentctl-daemon binary into ${BIN_DIR}..."
mkdir -p "${BIN_DIR}"
cp "${SCRIPT_DIR}/target/release/agentctl-daemon" "${BIN_DIR}/agentctl-daemon"
chmod +x "${BIN_DIR}/agentctl-daemon"

# 3. Setup Python virtual environment
echo "[3/4] Ensuring Python virtual environment is configured..."
if [ ! -d "${SCRIPT_DIR}/.venv" ]; then
    python3 -m venv "${SCRIPT_DIR}/.venv"
fi
"${SCRIPT_DIR}/.venv/bin/pip" install -e "${SCRIPT_DIR}"

# 4. Install systemd user units
echo "[4/4] Installing systemd user units..."
mkdir -p "${SYSTEMD_USER_DIR}"
cp "${SCRIPT_DIR}/systemd/agentctl-daemon.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/systemd/agentctl-ui.service" "${SYSTEMD_USER_DIR}/"

echo ""
echo "Installation complete!"
echo "To start services with systemd:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now agentctl-daemon"
echo "  systemctl --user enable --now agentctl-ui"
echo ""
echo "To test manually in terminal:"
echo "  Run daemon:  ~/.cargo/bin/agentctl-daemon"
echo "  Trigger UI:  ${SCRIPT_DIR}/.venv/bin/python -m agentctl.hotkey trigger"
echo "               (or activate venv first: source .venv/bin/activate)"

