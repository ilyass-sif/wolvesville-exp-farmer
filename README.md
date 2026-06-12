# Wolvesville EXP Farmer

A high-performance, lightweight, automated Wolvesville experience (EXP) farming bot. It connects directly via WebSocket to Wolvesville game servers, detects and joins custom English "vill win" lobbies, participates in games minimalistically, and collects EXP.

## Architecture

This repository contains three main components working together:

1. **`exp_farmer.py`**: The core matchmaking and farming loop. It runs a local server on port `5589` to receive lobby updates, queries matchmaking APIs, connects using `wolvesville_client.py`, and automates basic actions in-game to earn EXP.
2. **`token_server.py`**: A local HTTP server running on port `5588` that listens for fresh authentication tokens (`firebase_token` and `cf_jwt`) and dynamically updates the configurations so the farmer can reconnect with fresh credentials.
3. **`headless.py`**: An automation script powered by `nodriver`. It launches Chromium using your existing browser profile, navigates to Wolvesville, intercepts the Cloudflare Turnstile token verification requests, and forwards them to the `token_server.py` to keep the bot authenticated indefinitely. **To run this headless on a VPS or CLI-only environment, it is wrapped in an X Virtual Framebuffer (`xvfb-run`).**

### Helper Scripts Included:
* **`setup_vps.sh`**: Automates installation of all required system dependencies (like `xvfb`, `chromium-browser`, and `tmux`) and configures your virtual environment.
* **`start_tmux.sh`**: Spins up the token server, headless bypass (running under `xvfb-run`), and the EXP farmer dashboard in a detached persistent `tmux` session for 24/7 background running.
* **`stop_farmer.sh`**: Gracefully kills the tmux session and cleans up any lingering Python/Xvfb processes.

---

## Setup & Installation

### Prerequisites
* Python 3.10 or higher
* Debian/Ubuntu-based VPS (or local Linux machine with `xvfb` and `chromium`)

### 1. Run Setup Script (Recommended)
This script will automatically install system packages, `xvfb`, `chromium-browser`, set up your python virtual environment, and install dependencies:

```bash
chmod +x setup_vps.sh start_tmux.sh stop_farmer.sh
./setup_vps.sh
```

*(Alternatively, you can manually install the packages: `sudo apt install -y xvfb chromium-browser tmux` and run `pip install -r requirements.txt` within your virtualenv.)*

### 2. Configure Settings
Copy the example configuration file and fill in your details:

```bash
cp config.json.example config.json
```

Edit `config.json`:
* `BOT_USERNAME`: Your Wolvesville player username.
* `WOLVESVILLE_TOKEN` & `WOLVESVILLE_CF_JWT`: These will be automatically filled by `headless.py` or the Tampermonkey forwarder, but you can paste them initially if you have them.

---

## Running the EXP Farmer

### Running 24/7 in background (via Tmux + Xvfb)
Simply launch the session manager:

```bash
./start_tmux.sh
```

This starts all three components in separate tmux windows.
* Window 0: `token_server.py`
* Window 1: `headless.py` (wrapped under `xvfb-run` for headless execution)
* Window 2: `exp_farmer.py` (The dashboard interface)

To view the active console dashboard, attach to tmux:
```bash
tmux attach-session -t wolvesville
```
*(To detach without stopping the bot, press `Ctrl+B` then `D`)*

To stop the farmer and clean up all background processes:
```bash
./stop_farmer.sh
```

---

## Technical Details

* **Headless Display**: The browser needs to render the Turnstile challenge visually to solve it. On systems without a GUI display, we run the browser inside **`xvfb`** (X Virtual Framebuffer), creating a virtual screen in memory (`xvfb-run --server-args="-screen 0 1024x768x24" python3 headless.py`).
* **WebSocket Transport**: The bot bypasses the web app UI entirely. It connects to `wss://game.api-wolvesville.com/socket.io/` using pure WebSocket frames structured under Engine.IO v4.
* **Lobby Filtering**: The farmer translates decorative custom names (e.g. converting small caps `ᴠɪʟʟ ᴡɪɴ` to standard characters) to locate custom EXP lobbies.
* **Token Rotation**: Wolvesville uses short-lived tokens (expires in ~1 hour). The headless hook automatically captures the new credentials whenever Turnstile executes a background refresh.
