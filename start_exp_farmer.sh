#!/bin/bash

# Activate virtualenv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Clean up any leftover Xvfb locks
rm -f /tmp/.X99-lock

echo "[*] Starting Wolvesville EXP Farmer Stack..."

# Run headless.py under xvfb-run in the background
if command -v xvfb-run &> /dev/null; then
    echo "[*] Launching headless browser token grabber with Xvfb..."
    xvfb-run --server-args="-screen 0 1024x768x24" python3 headless.py > headless.log 2>&1 &
    HEADLESS_PID=$!
else
    echo "[!] Warning: xvfb-run not found. Running headless.py in headed mode in background..."
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
