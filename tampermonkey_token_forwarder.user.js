// ==UserScript==
// @name         Wolvesville Token Forwarder
// @namespace    wolvesville-bot
// @version      1.1
// @description  Intercepts Cloudflare Turnstile token refresh and forwards tokens to the bot
// @match        https://wolvesville.com/*
// @match        https://www.wolvesville.com/*
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      localhost
// @run-at       document-start
// ==/UserScript==

(function () {
    'use strict';

    const BOT_ENDPOINT = 'http://localhost:5588/tokens';
    const LOBBIES_ENDPOINT = 'http://localhost:5589/lobbies';
    const W = unsafeWindow;  // The REAL page window, not the Tampermonkey sandbox

    // ── Intercept XHR on the real page ─────────────────────────────────
    const origOpen = W.XMLHttpRequest.prototype.open;
    const origSend = W.XMLHttpRequest.prototype.send;

    W.XMLHttpRequest.prototype.open = function (method, url, ...rest) {
        this._url = url;
        this._method = method;
        return origOpen.call(this, method, url, ...rest);
    };

    W.XMLHttpRequest.prototype.send = function (body) {
        if (this._url && this._url.includes('cloudflareTurnstile/verify')) {
            console.log('[WV-Forwarder] 🎯 Intercepted XHR to cloudflareTurnstile/verify');
            let reqBody = null;
            try { reqBody = JSON.parse(body); } catch (_) {}

            this.addEventListener('load', function () {
                try {
                    const resp = JSON.parse(this.responseText);
                    if (resp.jwt && reqBody && reqBody.idToken) {
                        forward(reqBody.idToken, resp.jwt);
                    }
                } catch (e) {
                    console.warn('[WV-Forwarder] XHR parse error:', e);
                }
            });
        }
        if (this._url && this._url.includes('api/public/game/custom')) {
            console.log('[WV-Forwarder] 🎯 Intercepted XHR to custom lobbies');
            this.addEventListener('load', function () {
                try {
                    const resp = JSON.parse(this.responseText);
                    if (resp.openGames) {
                        forwardLobbies(resp.openGames);
                    }
                } catch (e) {
                    console.warn('[WV-Forwarder] Lobbies XHR parse error:', e);
                }
            });
        }

        return origSend.call(this, body);
    };

    // ── Intercept fetch on the real page ───────────────────────────────
    const origFetch = W.fetch;
    W.fetch = function (input, init) {
        const url = typeof input === 'string' ? input : input?.url || '';

        if (url.includes('cloudflareTurnstile/verify')) {
            console.log('[WV-Forwarder] 🎯 Intercepted fetch to cloudflareTurnstile/verify');

            return origFetch.call(W, input, init).then(response => {
                const clone = response.clone();
                let reqBody = null;
                try { reqBody = init?.body ? JSON.parse(init.body) : null; } catch (_) {}

                clone.json().then(respBody => {
                    if (respBody.jwt && reqBody && reqBody.idToken) {
                        forward(reqBody.idToken, respBody.jwt);
                    }
                }).catch(e => console.warn('[WV-Forwarder] fetch parse error:', e));

                return response;
            });
        }

        if (url.includes('api/public/game/custom')) {
            console.log('[WV-Forwarder] 🎯 Intercepted fetch to custom lobbies');

            return origFetch.call(W, input, init).then(response => {
                const clone = response.clone();
                clone.json().then(respBody => {
                    if (respBody.openGames) {
                        forwardLobbies(respBody.openGames);
                    }
                }).catch(e => console.warn('[WV-Forwarder] Lobbies fetch parse error:', e));

                return response;
            });
        }

        return origFetch.call(W, input, init);
    };

    // ── Forward to bot ─────────────────────────────────────────────────
    function forward(idToken, cfJwt) {
        const payload = JSON.stringify({
            firebase_token: idToken,
            cf_jwt: cfJwt,
            timestamp: Date.now()
        });

        console.log('[WV-Forwarder] 🔑 Tokens captured! Forwarding to bot...');
        console.log('[WV-Forwarder]   idToken: ...' + idToken.slice(-30));
        console.log('[WV-Forwarder]   cf_jwt:  ...' + cfJwt.slice(-30));

        GM_xmlhttpRequest({
            method: 'POST',
            url: BOT_ENDPOINT,
            headers: { 'Content-Type': 'application/json' },
            data: payload,
            onload: (r) => console.log('[WV-Forwarder] ✅ Sent to bot, status:', r.status),
            onerror: (e) => console.warn('[WV-Forwarder] ❌ Bot unreachable (is it running?)', e)
        });
    }

    function forwardLobbies(games) {
        console.log(`[WV-Forwarder] 🎮 Found ${games.length} custom lobbies! Forwarding...`);
        GM_xmlhttpRequest({
            method: 'POST',
            url: LOBBIES_ENDPOINT,
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify({ lobbies: games }),
            onload: (r) => console.log('[WV-Forwarder] ✅ Lobbies sent to bot, status:', r.status),
            onerror: (e) => console.warn('[WV-Forwarder] ❌ Lobbies bot unreachable (is it running?)', e)
        });
    }

    console.log('[WV-Forwarder] 🎯 Token & Lobby interceptor active (v1.2 — unsafeWindow patched)');
})();
