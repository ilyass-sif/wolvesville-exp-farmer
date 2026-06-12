#!/usr/bin/env bash
# ==============================================================================
# Wolvesville Bot Tmux Session Orchestrator
# ==============================================================================
# Starts all three necessary components in a persistent, detached Tmux session.
# Use this script to run the bot 24/7 and easily attach to view the console.
# ==============================================================================

SESSION_NAME="wolvesville"
VENV_ACTIVATE=".venv/bin/activate"

# Color constants
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Ensure tmux is installed
if ! command -v tmux &> /dev/null; then
    echo -e "${RED}❌ Tmux is not installed! Run ./setup_vps.sh first.${NC}"
    exit 1
fi

# Check if session already exists
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo -e "${YELLOW}⚠ A tmux session named '${SESSION_NAME}' is already running.${NC}"
    echo -e "Options:"
    echo -e "  [1] Attach to the existing session (view dashboard)"
    echo -e "  [2] Kill the existing session and start fresh"
    echo -e "  [3] Exit"
    read -rp "Select option [1-3]: " option

    case "$option" in
        1)
            tmux attach-session -t "$SESSION_NAME"
            exit 0
            ;;
        2)
            echo -e "${RED}Killing existing tmux session '${SESSION_NAME}'...${NC}"
            tmux kill-session -t "$SESSION_NAME"
            sleep 1
            ;;
        *)
            echo -e "Exiting without changes."
            exit 0
            ;;
    esac
fi

echo -e "${CYAN}Starting new Wolvesville detached tmux session: '${SESSION_NAME}'...${NC}"

# Create the session and start Window 1 (Token Server)
# We execute 'bash' inside tmux, source our venv, and run the file
tmux new-session -d -s "$SESSION_NAME" -n "token_server"
tmux send-keys -t "${SESSION_NAME}:token_server" "source $VENV_ACTIVATE" C-m
tmux send-keys -t "${SESSION_NAME}:token_server" "python3 token_server.py" C-m
echo -e "${GREEN}✔ Window 1: Token Server started.${NC}"
sleep 1.5

# Create Window 2 (Headless Browser verifying Turnstile under Xvfb)
tmux new-window -t "$SESSION_NAME" -n "headless"
tmux send-keys -t "${SESSION_NAME}:headless" "source $VENV_ACTIVATE" C-m
tmux send-keys -t "${SESSION_NAME}:headless" "xvfb-run --server-args=\"-screen 0 1024x768x24\" python3 headless.py" C-m
echo -e "${GREEN}✔ Window 2: Headless Turnstile Client (Xvfb) started.${NC}"
sleep 2

# Create Window 3 (EXP Farmer Dashboard)
tmux new-window -t "$SESSION_NAME" -n "farmer"
tmux send-keys -t "${SESSION_NAME}:farmer" "source $VENV_ACTIVATE" C-m
tmux send-keys -t "${SESSION_NAME}:farmer" "python3 exp_farmer.py" C-m
echo -e "${GREEN}✔ Window 3: EXP Farmer Dashboard started.${NC}"

echo -e "\n${GREEN}🚀 ALL PROCESSES STARTED SUCCESSFULLY IN DETACHED TMUX SESSION!${NC}"
echo -e "--------------------------------------------------------"
echo -e "To view the beautiful Live Dashboard, run:"
echo -e "   ${CYAN}tmux attach-session -t $SESSION_NAME${NC}"
echo -e "--------------------------------------------------------"
echo -e "To navigate windows inside Tmux:"
echo -e "   - Next window:     Press ${YELLOW}Ctrl+B${NC} then ${YELLOW}N${NC}"
echo -e "   - Previous window: Press ${YELLOW}Ctrl+B${NC} then ${YELLOW}P${NC}"
echo -e "   - Specific window: Press ${YELLOW}Ctrl+B${NC} then window number (0=Token Server, 1=Headless, 2=Farmer)"
echo -e "   - Detach & close:  Press ${YELLOW}Ctrl+B${NC} then ${YELLOW}D${NC} (bot keeps running in the background!)"
echo -e "--------------------------------------------------------\n"
