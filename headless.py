import asyncio
import nodriver as uc
import json
import os
import time
import aiohttp

async def capture_turnstile():
    # Dynamically find the chromium profile path
    home = os.path.expanduser("~")
    PROFILE_PATH = os.path.join(home, ".config/chromium")
    TOKEN_SERVER_URL = "http://127.0.0.1:5588/tokens"
    
    print(f"[*] Profile Path: {PROFILE_PATH}")
    print("[*] Launching browser...")
    
    # Launch nodriver with headed mode and efficiency-focused arguments
    browser = await uc.start(
        headless=False,
        user_data_dir=PROFILE_PATH,
        browser_args=[
            "--window-size=1000,700",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--ozone-platform=x11"
        ]
    )
    
    page = browser.main_tab

    print("[*] Blocking unnecessary third-party trackers for efficiency...")
    await page.send(uc.cdp.network.set_blocked_ur_ls(urls=[
        "*sentry.io*", "*facebook.com*", "*twitter.com*"
    ]))

    print("[*] Injecting JavaScript XHR/Fetch hook...")
    
    # This payload perfectly mirrors the Tampermonkey extraction logic
    # It hooks both XHR and Fetch, extracts idToken/jwt, and formats the exact payload.
    js_hook = """
    const W = window;
    W.__captured_auth_state = null; // Python will poll this

    // ── Intercept XHR ─────────────────────────────────────────────────
    const origOpen = W.XMLHttpRequest.prototype.open;
    const origSend = W.XMLHttpRequest.prototype.send;

    W.XMLHttpRequest.prototype.open = function (method, url, ...rest) {
        this._url = url;
        this._method = method;
        return origOpen.call(this, method, url, ...rest);
    };

    W.XMLHttpRequest.prototype.send = function (body) {
        if (this._url && this._url.includes('cloudflareTurnstile/verify')) {
            let reqBody = null;
            try { reqBody = JSON.parse(body); } catch (_) {}

            this.addEventListener('load', function () {
                try {
                    const resp = JSON.parse(this.responseText);
                    if (resp.jwt && reqBody && reqBody.idToken) {
                        W.__captured_auth_state = {
                            firebase_token: reqBody.idToken,
                            cf_jwt: resp.jwt,
                            timestamp: Date.now()
                        };
                    }
                } catch (e) {}
            });
        }
        return origSend.call(this, body);
    };

    // ── Intercept fetch ───────────────────────────────────────────────
    const origFetch = W.fetch;
    W.fetch = function (input, init) {
        const url = typeof input === 'string' ? input : input?.url || '';

        if (url.includes('cloudflareTurnstile/verify')) {
            return origFetch.call(W, input, init).then(response => {
                const clone = response.clone();
                let reqBody = null;
                try { reqBody = init?.body ? JSON.parse(init.body) : null; } catch (_) {}

                clone.json().then(respBody => {
                    if (respBody.jwt && reqBody && reqBody.idToken) {
                        W.__captured_auth_state = {
                            firebase_token: reqBody.idToken,
                            cf_jwt: respBody.jwt,
                            timestamp: Date.now()
                        };
                    }
                }).catch(e => {});

                return response;
            });
        }

        return origFetch.call(W, input, init);
    };
    """

    # Inject the hook into every new document BEFORE the site loads
    await page.send(uc.cdp.page.enable())
    await page.send(uc.cdp.page.add_script_to_evaluate_on_new_document(source=js_hook))

    print("[*] Navigating to Wolvesville...")
    await page.get("https://www.wolvesville.com/")



    print("[*] JS Interceptor active. Polling window object for tokens...")
    print("[*] This script will stay open and refresh periodically to keep tokens fresh.")
    print("[*] Press Ctrl+C to stop.")

    last_refresh_time = time.time()

    try:
        while True:
            # Poll the JavaScript environment to see if the hook caught anything
            tokens = await page.evaluate("window.__captured_auth_state")
            
            if tokens:
                # Translate the CDP-style list of lists into a clean dictionary the server expects
                try:
                    clean_tokens = {item[0]: item[1].get('value') for item in tokens}
                except (IndexError, TypeError, AttributeError):
                    clean_tokens = tokens # Fallback if it's already a dict

                print(f"\n[*] SUCCESS: Tokens captured via JS Hook!")
                print(f"[*] Payload extracted: {json.dumps(clean_tokens)}")
                print(f"[*] Forwarding to token server at {TOKEN_SERVER_URL}...")
                
                async with aiohttp.ClientSession() as session:
                    # Sending the translated dictionary
                    async with session.post(TOKEN_SERVER_URL, json=clean_tokens) as resp:
                        if resp.status == 200:
                            print("[*] ✅ Tokens successfully forwarded to server.")
                        else:
                            print(f"[!] ❌ Failed to forward tokens. Status: {resp.status}")
                
                # Reset the variable in JS so we don't spam the server continuously
                await page.evaluate("window.__captured_auth_state = null")

            # Refresh every 10 minutes (600 seconds) to trigger new token generation
            if time.time() - last_refresh_time > 600:
                print("\n[*] Refreshing page to renew tokens...")
                await page.get("https://www.wolvesville.com/")
                last_refresh_time = time.time()
                
            # Rest for a second before checking again to save CPU
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        print("[*] Stopping...")
    finally:
        print("[*] Closing browser.")
        browser.stop()

if __name__ == "__main__":
    uc.loop().run_until_complete(capture_turnstile())
