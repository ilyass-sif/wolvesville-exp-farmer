# Wolvesville EXP Farmer

A high-performance, lightweight, automated Wolvesville experience (EXP) farming bot. It connects directly via WebSocket to Wolvesville game servers, detects and joins custom English "vill win" lobbies, participates in games minimalistically, and collects EXP.

## Architecture

This repository contains three main components working together:

1. **`exp_farmer.py`**: The core matchmaking and farming loop. It runs a local server on port `5589` to receive lobby updates, queries matchmaking APIs, connects using `wolvesville_client.py`, and automates basic actions in-game to earn EXP.
2. **`token_server.py`**: A local HTTP server running on port `5588` that listens for fresh authentication tokens (`firebase_token` and `cf_jwt`) and dynamically updates the configurations so the farmer can reconnect with fresh credentials.
3. **`headless.py`**: A headless/headed automation script powered by `nodriver`. It automatically launches Chromium using your existing browser profile, navigates to Wolvesville, intercepts the Cloudflare Turnstile token verification requests, and forwards them to the `token_server.py` to keep the bot authenticated indefinitely.

### Additional Files Included:
* **`wolvesville_client.py`**: Direct WebSocket client handling Wolvesville server-to-client events.
* **`ws_transport.py`**: Underlying Engine.IO / Socket.IO framing protocol.
* **`belief_engine.py` / `inference.py`**: Bayesian Belief Network (BBN) + Constraint Satisfaction Problem (CSP) solvers used for predicting player roles in-game.
* **`slang.py`**: Maps Wolvesville chat abbreviations to standard terms.
* **`logger.py`**: Beautiful terminal output utility powered by `rich`.
* **`tampermonkey_token_forwarder.user.js`**: An alternative to `headless.py` if you prefer to play/browse manually. Installs as a user script in Tampermonkey to forward tokens to your local bot server.

---

## Setup & Installation

### Prerequisites
* Python 3.10 or higher
* Google Chrome or Chromium (needed for `headless.py`)

### 1. Clone & Install Dependencies
Initialize a virtual environment and install the required Python packages:

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

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

To run the full farming stack, you should start the components in separate terminals (or using `tmux`/background tasks):

### Step 1: Start the Token Server & Farmer
The EXP farmer automatically spins up the token server in the background to handle credential refreshes.

```bash
python3 exp_farmer.py
```

### Step 2: Keep Tokens Fresh (Choose Option A or B)

#### Option A: Headless Browser Automation (Recommended)
This script uses `nodriver` to spin up a browser, bypass Turnstile challenges, and forward fresh tokens to the farmer:

```bash
python3 headless.py
```

#### Option B: Tampermonkey Userscript
If you prefer to run the game in your own daily-use browser:
1. Install the [Tampermonkey Extension](https://www.tampermonkey.net/).
2. Create a new user script and paste the contents of `tampermonkey_token_forwarder.user.js`.
3. Open [Wolvesville](https://www.wolvesville.com/) in your browser. The script will intercept the tokens on load/refresh and send them to `http://localhost:5588/tokens`.

---

## Technical Details

* **WebSocket Transport**: The bot bypasses the web app UI entirely. It connects to `wss://game.api-wolvesville.com/socket.io/` using pure WebSocket frames structured under Engine.IO v4.
* **Lobby Filtering**: The farmer translates decorative custom names (e.g. converting small caps `ᴠɪʟʟ ᴡɪɴ` to standard characters) to locate custom EXP lobbies.
* **Token Rotation**: Wolvesville uses short-lived tokens (expires in ~1 hour). The headless hook automatically captures the new credentials whenever Turnstile executes a background refresh.
