#!/usr/bin/env bash
# ==============================================================================
# Wolvesville Bot VPS Setup Script
# ==============================================================================
# Automates the setup of system dependencies, Google Chrome, Xvfb, Tmux,
# and Python requirements for 24/7 running on Google Cloud VM (Ubuntu/Debian).
# ==============================================================================

set -e

# Harmonious colors for logs
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}========================================================"
echo -e "         WOLVESVILLE BOT VPS AUTO-SETUP SCRIPT          "
echo -e "========================================================${NC}\n"

# 1. Update package lists
echo -e "${YELLOW}[1/5] Updating system packages...${NC}"
sudo apt-get update -y

# 2. Install essential system dependencies
echo -e "${YELLOW}[2/5] Installing core dependencies (Xvfb, Tmux, Python venv, etc.)...${NC}"
sudo apt-get install -y \
    wget \
    curl \
    unzip \
    tmux \
    xvfb \
    python3-pip \
    python3-venv \
    libxi6 \
    libgconf-2-4 \
    libnss3 \
    libxss1 \
    libasound2 \
    libgbm1 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    xdg-utils

# 3. Install Chromium Browser
echo -e "${YELLOW}[3/5] Installing Chromium Browser...${NC}"
if ! command -v chromium-browser &> /dev/null && ! command -v chromium &> /dev/null; then
    # Try installing chromium-browser (Ubuntu) or chromium (Debian)
    sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
    echo -e "${GREEN}✔ Chromium Browser installed successfully!${NC}"
else
    echo -e "${GREEN}✔ Chromium Browser is already installed.${NC}"
fi

# Ensure 'chromium-browser' executable command is available on the VPS by creating a symlink
if ! command -v chromium-browser &> /dev/null && command -v chromium &> /dev/null; then
    echo -e "${YELLOW}Creating system symlink from 'chromium' to 'chromium-browser'...${NC}"
    sudo ln -sf "$(which chromium)" /usr/bin/chromium-browser || true
    echo -e "${GREEN}✔ Symlink created! 'chromium-browser' is now active on your VPS.${NC}"
fi

# 4. Initialize Python Virtual Environment
echo -e "${YELLOW}[4/5] Setting up Python virtual environment...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "${GREEN}✔ Virtual environment (.venv) created.${NC}"
else
    echo -e "${GREEN}✔ Virtual environment (.venv) already exists.${NC}"
fi

# Activate venv and install dependencies
source .venv/bin/activate
echo -e "${YELLOW}Installing lightweight pip requirements...${NC}"
pip install --upgrade pip
pip install nodriver aiohttp websockets rich

echo -e "${GREEN}✔ Pip dependencies installed successfully!${NC}"

# 5. Fix permissions for browser_data
echo -e "${YELLOW}[5/5] Checking browser profile data permissions...${NC}"
if [ -d "browser_data" ]; then
    chmod -R 755 browser_data
    echo -e "${GREEN}✔ Permissions aligned on 'browser_data'.${NC}"
else
    echo -e "${YELLOW}⚠ Warning: 'browser_data' folder not found. Please ensure you upload/copy it to the bot folder.${NC}"
fi

echo -e "\n${GREEN}========================================================"
echo -e "       SETUP COMPLETED SUCCESSFULLY! READY TO FARM      "
echo -e "========================================================${NC}"
echo -e "To start farming, you can use either Tmux or systemd services."
echo -e "Review the generated guides or use './start_tmux.sh' to launch."
echo -e "========================================================\n"
