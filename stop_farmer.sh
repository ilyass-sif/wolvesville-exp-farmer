#!/usr/bin/env bash
# ==============================================================================
# Wolvesville Bot Graceful Shutdown Script
# ==============================================================================
# Safely stops the tmux session and cleans up any lingering Python/Xvfb processes.
# ==============================================================================

SESSION_NAME="wolvesville"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Shutting down Wolvesville EXP Farmer...${NC}"

# Kill tmux session if running
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "Stopping tmux session '${SESSION_NAME}'..."
    tmux kill-session -t "$SESSION_NAME"
    echo -e "${GREEN}✔ Tmux session killed.${NC}"
else
    echo -e "No active tmux session named '${SESSION_NAME}' found."
fi

# Clean up lingering python/xvfb processes just in case
echo -e "Cleaning up lingering processes..."

# Graceful termination
pkill -f "python3 exp_farmer.py" || true
pkill -f "python3 headless.py" || true
pkill -f "python3 token_server.py" || true
pkill -f "xvfb-run" || true

sleep 1

# Force kill if still alive
pkill -9 -f "python3 exp_farmer.py" || true
pkill -9 -f "python3 headless.py" || true
pkill -9 -f "python3 token_server.py" || true

echo -e "${GREEN}✔ All background processes cleaned up!${NC}"
echo -e "${GREEN}========================================================${NC}"
echo -e "                   BOT SUCCESSFULLY STOPPED             "
echo -e "${GREEN}========================================================${NC}\n"
