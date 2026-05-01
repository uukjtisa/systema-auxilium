#!/bin/bash

VENV_NAME=".venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo " Systema Auxilium Environment Setup"
echo " Linux / Debian"
echo "========================================"
echo ""

# ── [1/5] Create virtual environment ──────────────────────────────────────────
echo "[1/5] Checking for Python 3.10..."

if command -v python3.10 &>/dev/null; then
    echo "       Python 3.10 detected. Creating venv..."
    python3.10 -m venv "$VENV_NAME"
else
    echo "       Python 3.10 not found. Falling back to default python3..."
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

# add_autostart.sh — uses ~/.config/autostart (.desktop entry, works on GNOME/KDE/XFCE)
cat > "$SCRIPT_DIR/add_autostart.sh" << 'EOF'
#!/bin/bash
# Adds Systema Auxilium to autostart via XDG autostart (.desktop file)
# Works on GNOME, KDE, XFCE, and most desktop environments

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SH="$SCRIPT_DIR/run.sh"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/systema-auxilium.desktop"

mkdir -p "$AUTOSTART_DIR"

cat > "$DESKTOP_FILE" << DESKTOP
[Desktop Entry]
Type=Application
Name=Systema Auxilium
Exec=bash "$RUN_SH"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=Systema Auxilium AI Desktop Assistant
DESKTOP

echo "Autostart entry created at: $DESKTOP_FILE"
echo "Systema Auxilium will launch on next login."
EOF

# remove_autostart.sh
cat > "$SCRIPT_DIR/remove_autostart.sh" << 'EOF'
#!/bin/bash
# Removes Systema Auxilium autostart entry

DESKTOP_FILE="$HOME/.config/autostart/systema-auxilium.desktop"

if [ -f "$DESKTOP_FILE" ]; then
    rm "$DESKTOP_FILE"
    echo "Autostart entry removed: $DESKTOP_FILE"
else
    echo "No autostart entry found at: $DESKTOP_FILE"
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
