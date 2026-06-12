#!/bin/bash

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Clean up any leftover Xvfb locks
rm -f /tmp/.X99-lock

# Check and install Xvfb if missing
if ! command -v xvfb-run &> /dev/null; then
    echo "[!] Xvfb (xvfb-run) is not installed. Attempting to install automatically..."
    
    if command -v apt-get &> /dev/null; then
        echo "[*] Detected Debian/Ubuntu system. Running: sudo apt-get update && sudo apt-get install -y xvfb"
        sudo apt-get update && sudo apt-get install -y xvfb
    elif command -v dnf &> /dev/null; then
        echo "[*] Detected RHEL/Fedora system. Running: sudo dnf install -y xorg-x11-server-Xvfb"
        sudo dnf install -y xorg-x11-server-Xvfb
    elif command -v yum &> /dev/null; then
        echo "[*] Detected CentOS/RHEL system. Running: sudo yum install -y xorg-x11-server-Xvfb"
        sudo yum install -y xorg-x11-server-Xvfb
    elif command -v pacman &> /dev/null; then
        echo "[*] Detected Arch Linux system. Running: sudo pacman -S --noconfirm xorg-server-xvfb"
        sudo pacman -S --noconfirm xorg-server-xvfb
    elif command -v zypper &> /dev/null; then
        echo "[*] Detected openSUSE system. Running: sudo zypper install -y xorg-x11-server-extra"
        sudo zypper install -y xorg-x11-server-extra
    else
        echo "[!] No supported package manager found. Please install Xvfb manually."
    fi

    # Recheck installation
    if ! command -v xvfb-run &> /dev/null; then
        echo "[!] Installation failed or Xvfb still not found. Proceeding in headed mode..."
    else
        echo "[+] Xvfb successfully installed!"
    fi
fi

echo "[*] Starting Wolvesville EXP Farmer Stack..."

# Run headless.py under xvfb-run in the background
if command -v xvfb-run &> /dev/null; then
    echo "[*] Launching headless browser token grabber with Xvfb..."
    xvfb-run --server-args="-screen 0 1024x768x24" python3 headless.py > headless.log 2>&1 &
    HEADLESS_PID=$!
else
    echo "[!] Running headless.py in headed mode in background..."
    python3 headless.py > headless.log 2>&1 &
    HEADLESS_PID=$!
fi

# Trap exits to kill background headless browser
cleanup() {
    echo ""
    echo "[*] Shutting down..."
    if [ -n "$HEADLESS_PID" ]; then
        kill $HEADLESS_PID 2>/dev/null
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start the farmer in the foreground so we can see the Rich dashboard
python3 exp_farmer.py

# When farmer exits, trigger cleanup
cleanup
