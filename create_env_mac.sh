#!/bin/bash

VENV_NAME=".venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_LABEL="com.nic2007.systema-auxilium"
PLIST_PATH="$HOME/Library/LaunchAgents/$APP_LABEL.plist"

echo "========================================"
echo " Systema Auxilium Environment Setup"
echo " macOS"
echo "========================================"
echo ""

# ── [1/5] Create virtual environment ──────────────────────────────────────────
echo "[1/5] Checking for Python 3.10..."

if command -v python3.10 &>/dev/null; then
    echo "       Python 3.10 detected. Creating venv..."
    python3.10 -m venv "$VENV_NAME"
elif command -v brew &>/dev/null && brew list python@3.10 &>/dev/null; then
    echo "       Found Python 3.10 via Homebrew. Creating venv..."
    "$(brew --prefix python@3.10)/bin/python3.10" -m venv "$VENV_NAME"
else
    echo "       Python 3.10 not found. Falling back to default python3..."
    echo "       (Tip: install via 'brew install python@3.10' for best compatibility)"
    python3 -m venv "$VENV_NAME"
fi

# ── [2/5] Create helper scripts ────────────────────────────────────────────────
echo ""
echo "[2/5] Creating helper shell scripts..."

# open_env.sh
cat > "$SCRIPT_DIR/open_env.sh" << 'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash --rcfile <(echo "source \"$SCRIPT_DIR/.venv/bin/activate\"; echo 'Systema Auxilium venv activated.'")
EOF

# run.sh
cat > "$SCRIPT_DIR/run.sh" << 'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
python "$SCRIPT_DIR/main.py"
EOF

# add_autostart.sh — uses launchd plist (native macOS autostart)
cat > "$SCRIPT_DIR/add_autostart.sh" << OUTER
#!/bin/bash
# Adds Systema Auxilium to autostart via launchd (native macOS)

SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
RUN_SH="\$SCRIPT_DIR/run.sh"
APP_LABEL="com.nic2007.systema-auxilium"
PLIST_PATH="\$HOME/Library/LaunchAgents/\$APP_LABEL.plist"

mkdir -p "\$HOME/Library/LaunchAgents"

cat > "\$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>\$APP_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>\$RUN_SH</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>\$HOME/Library/Logs/systema-auxilium.log</string>
    <key>StandardErrorPath</key>
    <string>\$HOME/Library/Logs/systema-auxilium-error.log</string>
</dict>
</plist>
PLIST

launchctl load "\$PLIST_PATH"
echo "Autostart registered with launchd: \$PLIST_PATH"
echo "Systema Auxilium will launch on next login."
echo "Logs will be written to ~/Library/Logs/systema-auxilium.log"
OUTER

# remove_autostart.sh
cat > "$SCRIPT_DIR/remove_autostart.sh" << 'EOF'
#!/bin/bash
# Removes Systema Auxilium from launchd autostart

APP_LABEL="com.nic2007.systema-auxilium"
PLIST_PATH="$HOME/Library/LaunchAgents/$APP_LABEL.plist"

if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH"
    rm "$PLIST_PATH"
    echo "Autostart entry removed: $PLIST_PATH"
else
    echo "No autostart entry found at: $PLIST_PATH"
fi
EOF

# Make all scripts executable
chmod +x "$SCRIPT_DIR/open_env.sh"
chmod +x "$SCRIPT_DIR/run.sh"
chmod +x "$SCRIPT_DIR/add_autostart.sh"
chmod +x "$SCRIPT_DIR/remove_autostart.sh"

echo "   - open_env.sh"
echo "   - run.sh"
echo "   - add_autostart.sh"
echo "   - remove_autostart.sh"

# ── [3/5] Activate venv ────────────────────────────────────────────────────────
echo ""
echo "[3/5] Activating virtual environment..."
source "$SCRIPT_DIR/$VENV_NAME/bin/activate"

# ── [4/5] Install dependencies ─────────────────────────────────────────────────
echo ""
echo "[4/5] Installing dependencies from requirements.txt..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    pip install -r "$SCRIPT_DIR/requirements.txt"
    if [ $? -eq 0 ]; then
        echo "Dependencies installed successfully!"
    else
        echo "WARNING: Some dependencies failed to install."
    fi
else
    echo "WARNING: No requirements.txt found. Skipping dependency installation."
fi

# ── [5/5] Verify Python version ────────────────────────────────────────────────
echo ""
echo "[5/5] Verifying Python version in venv..."
python --version

echo ""
echo "========================================"
echo " Setup Complete!"
echo "========================================"
echo ""
echo "Your environment is ready! You can now:"
echo "  - Run the app:        bash run.sh"
echo "  - Open venv terminal: bash open_env.sh"
echo "  - Enable autostart:   bash add_autostart.sh"
echo "  - Disable autostart:  bash remove_autostart.sh"
echo ""
echo "Note: On first run, macOS may ask for permission to"
echo "control your computer. Grant it in:"
echo "System Settings → Privacy & Security → Accessibility"
echo ""
