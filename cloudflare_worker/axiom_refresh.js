/**
 * Axiom Token Refresh + Login Relay
 * Deployed on Cloudflare Workers (free tier: 100k req/day)
 *
 * Routes:
 *   POST /             -- refresh existing tokens (all 10 servers x 6 paths)
 *   POST /login/step1  -- forward password login to Axiom (triggers OTP email)
 *   POST /login/step2  -- forward OTP to Axiom, returns new tokens
 *
 * Flow for auto-restore when refresh fails:
 *   1. Railway bot hashes password (PBKDF2) locally
 *   2. Railway calls POST /login/step1 with { secret, email, b64_password }
 *   3. Worker forwards to Axiom, extracts otp_jwt from Set-Cookie, returns it
 *   4. Railway reads OTP from Gmail IMAP (works fine from Railway -- no Cloudflare block)
 *   5. Railway calls POST /login/step2 with { secret, email, otp, otp_jwt, b64_password }
 *   6. Worker forwards to Axiom /login-otp, returns new access_token + refresh_token
 *
 * Environment variables (Workers dashboard -> Settings -> Variables):
 *   REFRESH_SECRET  -- must match AXIOM_REFRESH_RELAY_SECRET in Railway
 */

const AXIOM_SERVERS = [
  "https://api.axiom.trade",
  "https://api2.axiom.trade",
  "https://api3.axiom.trade",
  "https://api4.axiom.trade",
  "https://api5.axiom.trade",
  "https://api6.axiom.trade",
  "https://api7.axiom.trade",
  "https://api8.axiom.trade",
  "https://api9.axiom.trade",
  "https://api10.axiom.trade",
];

const REFRESH_PATHS = [
  "/refresh-access-token",
  "/refresh",
  "/auth/refresh",
  "/v2/refresh-access-token",
  "/api/refresh-access-token",
  "/token/refresh",
];

// All refresh endpoint combos: 6 paths x 10 servers = 60 candidates
// Ordered path-first so all servers are tried for the most common path before
// falling through to alternative paths.
const REFRESH_ENDPOINTS = [];
for (const path of REFRESH_PATHS) {
  for (const server of AXIOM_SERVERS) {
    REFRESH_ENDPOINTS.push(`${server}${path}`);
  }
}

const LOGIN_STEP1_ENDPOINTS = AXIOM_SERVERS.map(s => `${s}/login-password-v2`);
const LOGIN_STEP2_ENDPOINTS = AXIOM_SERVERS.map(s => `${s}/login-otp`);

const BROWSER_HEADERS = {
  "Accept":          "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  "Origin":          "https://axiom.trade",
  "Referer":         "https://axiom.trade/",
  "sec-fetch-dest":  "empty",
  "sec-fetch-mode":  "cors",
  "sec-fetch-site":  "same-site",
  "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
};

export default {
  async fetch(request, env) {
    const url    = new URL(request.url);
    const secret = env.REFRESH_SECRET || "changeme";

    // WebSocket proxy — must be checked BEFORE the POST-only guard
    if (url.pathname === "/ws-proxy") return handleWsProxy(request, env, secret);
    if (url.pathname === "/ds-proxy") return handleDsProxy(request, env, secret);
    if (url.pathname === "/rest-proxy") return handleRestProxy(request, env, secret);

    if (request.method !== "POST") {
      return json({ ok: false, error: "POST only" }, 405);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ ok: false, error: "Invalid JSON" }, 400);
    }

    if (!body.secret || body.secret !== secret) {
      return json({ ok: false, error: "Unauthorized" }, 401);
    }

    if (url.pathname === "/login/step1") return handleLoginStep1(body);
    if (url.pathname === "/login/step2") return handleLoginStep2(body);

    // Default route: token refresh
    return handleRefresh(body);
  },
};

// ---------------------------------------------------------------------------
// Route: POST / -- refresh existing tokens
// ---------------------------------------------------------------------------
async function handleRefresh(body) {
  const accessToken  = body.access_token  || "";
  const refreshToken = body.refresh_token || "";

  if (!refreshToken) {
    return json({ ok: false, error: "Missing refresh_token" }, 400);
  }

  const cookieHeader = `auth-refresh-token=${refreshToken}; auth-access-token=${accessToken}`;
  const headers = {
    ...BROWSER_HEADERS,
    "Content-Length": "0",
    "Cookie":         cookieHeader,
  };

  for (const endpoint of REFRESH_ENDPOINTS) {
    try {
      const resp = await fetch(endpoint, { method: "POST", headers, body: "" });

      let newAccess  = null;
      let newRefresh = null;

      const setCookies = resp.headers.getAll("Set-Cookie");
      for (const sc of setCookies) {
        if (!newAccess && sc.includes("auth-access-token=")) {
          const m = sc.match(/auth-access-token=([^;]+)/);
          if (m) newAccess = m[1].trim();
        }
        if (!newRefresh && sc.includes("auth-refresh-token=")) {
          const m = sc.match(/auth-refresh-token=([^;]+)/);
          if (m) newRefresh = m[1].trim();
        }
      }

      console.log(`REFRESH ${endpoint} -> ${resp.status} | token=${newAccess ? "YES" : "NO"}`);

      if (newAccess) {
        return json({
          ok:            true,
          access_token:  newAccess,
          refresh_token: newRefresh || refreshToken,
          endpoint,
        });
      }

      // 418 = token definitively invalid -- no point trying more
      if (resp.status === 418) break;

    } catch (e) {
      console.error(`${endpoint} error: ${e.message}`);
    }
  }

  return json({ ok: false, error: "All refresh endpoints failed" }, 502);
}

// ---------------------------------------------------------------------------
// Route: POST /login/step1
// Body: { secret, email, b64_password }
// Returns: { ok, otp_jwt, endpoint }
// The otp_jwt must be passed back to /login/step2.
// ---------------------------------------------------------------------------
async function handleLoginStep1(body) {
  const { email, b64_password } = body;
  if (!email || !b64_password) {
    return json({ ok: false, error: "Missing email or b64_password" }, 400);
  }

  // Axiom expects field named b64Password
  const payload = JSON.stringify({ email, b64Password: b64_password });
  const headers = {
    ...BROWSER_HEADERS,
    "Content-Type": "application/json",
    "Cookie":        "auth-otp-login-token=",
  };

  for (const endpoint of LOGIN_STEP1_ENDPOINTS) {
    try {
      const resp   = await fetch(endpoint, { method: "POST", headers, body: payload });
      const status = resp.status;

      console.log(`LOGIN_STEP1 ${endpoint} -> ${status}`);

      if (status === 200) {
        // Extract otp_jwt from Set-Cookie
        let otp_jwt = null;
        const setCookies = resp.headers.getAll("Set-Cookie");
        for (const sc of setCookies) {
          if (!otp_jwt && sc.includes("auth-otp-login-token=")) {
            const m = sc.match(/auth-otp-login-token=([^;]+)/);
            if (m) otp_jwt = m[1].trim();
          }
        }

        // Some variants return otp JWT in JSON body
        if (!otp_jwt) {
          try {
            const data = await resp.clone().json();
            otp_jwt = data.otpJwtToken || data.otp_jwt_token || data.jwt || null;
          } catch {}
        }

        return json({ ok: true, otp_jwt: otp_jwt || "", endpoint });
      }

      if (status === 404) continue;

      // 400/401 = bad credentials -- stop trying
      if (status === 400 || status === 401) {
        let errBody = null;
        try { errBody = await resp.json(); } catch {}
        return json({ ok: false, error: "Bad credentials", status, body: errBody });
      }

      // 422/429 etc -- try next server

    } catch (e) {
      console.error(`${endpoint} step1 error: ${e.message}`);
    }
  }

  return json({ ok: false, error: "All login step1 endpoints failed" }, 502);
}

// ---------------------------------------------------------------------------
// Route: POST /login/step2
// Body: { secret, email, otp, otp_jwt, b64_password }
// Returns: { ok, access_token, refresh_token, endpoint }
// ---------------------------------------------------------------------------
async function handleLoginStep2(body) {
  const { email, otp, otp_jwt, b64_password } = body;
  if (!email || !otp) {
    return json({ ok: false, error: "Missing email or otp" }, 400);
  }

  // Axiom step2 body: code (OTP), email, b64Password
  const payload = JSON.stringify({ code: otp, email, b64Password: b64_password || "" });
  const headers = {
    ...BROWSER_HEADERS,
    "Content-Type": "application/json",
    "Cookie":        otp_jwt ? `auth-otp-login-token=${otp_jwt}` : "auth-otp-login-token=",
  };

  for (const endpoint of LOGIN_STEP2_ENDPOINTS) {
    try {
      const resp   = await fetch(endpoint, { method: "POST", headers, body: payload });
      const status = resp.status;

      let newAccess  = null;
      let newRefresh = null;

      const setCookies = resp.headers.getAll("Set-Cookie");
      for (const sc of setCookies) {
        if (!newAccess && sc.includes("auth-access-token=")) {
          const m = sc.match(/auth-access-token=([^;]+)/);
          if (m) newAccess = m[1].trim();
        }
        if (!newRefresh && sc.includes("auth-refresh-token=")) {
          const m = sc.match(/auth-refresh-token=([^;]+)/);
          if (m) newRefresh = m[1].trim();
        }
      }

      // Some variants return tokens in JSON body
      if (!newAccess) {
        try {
          const data = await resp.clone().json();
          newAccess  = data.accessToken  || data.access_token  || null;
          newRefresh = data.refreshToken || data.refresh_token || newRefresh || null;
        } catch {}
      }

      console.log(`LOGIN_STEP2 ${endpoint} -> ${status} | token=${newAccess ? "YES" : "NO"}`);

      if (newAccess) {
        return json({
          ok:            true,
          access_token:  newAccess,
          refresh_token: newRefresh || "",
          endpoint,
        });
      }

      if (status === 404) continue;

      // 400/401/422 = bad OTP or expired -- stop trying
      if (status >= 400 && status < 500) {
        return json({ ok: false, error: `Login step2 rejected (${status})`, status });
      }

    } catch (e) {
      console.error(`${endpoint} step2 error: ${e.message}`);
    }
  }

  return json({ ok: false, error: "All login step2 endpoints failed" }, 502);
}

// ---------------------------------------------------------------------------
// Route: GET /ws-proxy  -- WebSocket proxy to Axiom clusters
// Query params:
//   s=<secret>            required
//   access_token=<jwt>    required
//   refresh_token=<jwt>   required
//   target=<host>         optional — "cluster9" (default) or "socket8"
//
// The Worker runs inside Cloudflare's own network, so Axiom clusters never see
// a datacenter IP — they see another Cloudflare edge node and let it through.
// Railway connects here (workers.dev bypasses Axiom's WAF) and gets a live feed.
//
// Supported targets:
//   cluster9 — trade/wallet/new_pairs feed (wss://cluster9.axiom.trade/)
//   socket8  — token price feed            (wss://socket8.axiom.trade/)
// ---------------------------------------------------------------------------
async function handleWsProxy(request, env, secret) {
  const url = new URL(request.url);

  // Validate secret
  const s = url.searchParams.get("s") || request.headers.get("X-Relay-Secret") || "";
  if (s !== secret) {
    return new Response("Unauthorized", { status: 401 });
  }

  // Require WebSocket upgrade
  if (request.headers.get("Upgrade") !== "websocket") {
    return new Response("Expected WebSocket upgrade", { status: 426 });
  }

  const accessToken  = url.searchParams.get("access_token")  || "";
  const refreshToken = url.searchParams.get("refresh_token") || "";
  const cookieHeader = `auth-access-token=${accessToken}; auth-refresh-token=${refreshToken}`;

  // Select upstream based on ?target= param
  const target = url.searchParams.get("target") || "cluster9";
  const UPSTREAM_HOSTS = {
    "cluster6": "https://cluster6.axiom.trade/",
    "cluster9": "https://cluster9.axiom.trade/",
    "socket8":  "https://socket8.axiom.trade/",
  };
  const upstreamUrl = UPSTREAM_HOSTS[target] || UPSTREAM_HOSTS["cluster9"];

  // Connect to upstream Axiom host from within Cloudflare's network
  let upstreamResp;
  try {
    upstreamResp = await fetch(upstreamUrl, {
      headers: {
        ...BROWSER_HEADERS,
        "Cookie":     cookieHeader,
        "Upgrade":    "websocket",
        "Connection": "Upgrade",
      },
    });
  } catch (e) {
    console.error(`ws-proxy upstream connect error [${target}]: ${e.message}`);
    return new Response(`Upstream connection failed: ${e.message}`, { status: 502 });
  }

  if (upstreamResp.status !== 101) {
    console.error(`ws-proxy upstream [${target}] returned ${upstreamResp.status}`);
    return new Response(`Upstream returned ${upstreamResp.status}`, { status: 502 });
  }

  const upstream = upstreamResp.webSocket;
  if (!upstream) {
    return new Response("No WebSocket in upstream response", { status: 502 });
  }

  // Create client-side WebSocketPair
  const { 0: client, 1: server } = new WebSocketPair();

  upstream.accept();
  server.accept();

  console.log(`ws-proxy open [${target}]`);

  // Diagnostic counters — log first few in each direction, then totals.
  let outCount = 0, inCount = 0;

  // Proxy: client → upstream
  server.addEventListener("message", ({ data }) => {
    outCount++;
    if (outCount <= 5) {
      const preview = typeof data === "string" ? data.slice(0, 200) : "[binary]";
      console.log(`ws-proxy [${target}] OUT#${outCount}: ${preview}`);
    }
    try { upstream.send(data); } catch (e) { console.error("client→upstream send error:", e.message); }
  });

  // Proxy: upstream → client
  upstream.addEventListener("message", ({ data }) => {
    inCount++;
    if (inCount <= 5) {
      const preview = typeof data === "string" ? data.slice(0, 200) : "[binary]";
      console.log(`ws-proxy [${target}] IN#${inCount}: ${preview}`);
    }
    try { server.send(data); } catch (e) { console.error("upstream→client send error:", e.message); }
  });

  server.addEventListener("close", ({ code, reason }) => {
    console.log(`ws-proxy [${target}] client closed: code=${code} reason=${reason} (out=${outCount} in=${inCount})`);
    try { upstream.close(code, reason); } catch {}
  });
  upstream.addEventListener("close", ({ code, reason }) => {
    console.log(`ws-proxy [${target}] upstream closed: code=${code} reason=${reason} (out=${outCount} in=${inCount})`);
    try { server.close(code, reason); } catch {}
  });

  server.addEventListener("error", (e)   => console.error("client WS error:",   e.message));
  upstream.addEventListener("error", (e) => console.error("upstream WS error:", e.message));

  return new Response(null, { status: 101, webSocket: client });
}

// ---------------------------------------------------------------------------
// Route: GET /ds-proxy  -- WebSocket proxy to DexScreener
// Query params:
//   s=<secret>   required
//
// DexScreener blocks Railway datacenter IPs on wss://io.dexscreener.com.
// This Worker runs on Cloudflare's edge and is not blocked.
// ---------------------------------------------------------------------------
async function handleDsProxy(request, env, secret) {
  const url = new URL(request.url);

  const s = url.searchParams.get("s") || request.headers.get("X-Relay-Secret") || "";
  if (s !== secret) {
    return new Response("Unauthorized", { status: 401 });
  }

  if (request.headers.get("Upgrade") !== "websocket") {
    return new Response("Expected WebSocket upgrade", { status: 426 });
  }

  const upstreamUrl = "https://io.dexscreener.com/dex/screener/v7/pairs/h24/1?rankBy[key]=trendingScoreH6&rankBy[order]=desc";

  let upstreamResp;
  try {
    upstreamResp = await fetch(upstreamUrl, {
      headers: {
        "Upgrade":    "websocket",
        "Connection": "Upgrade",
        "Origin":     "https://dexscreener.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
      },
    });
  } catch (e) {
    console.error(`ds-proxy upstream connect error: ${e.message}`);
    return new Response(`Upstream connection failed: ${e.message}`, { status: 502 });
  }

  if (upstreamResp.status !== 101) {
    console.error(`ds-proxy upstream returned ${upstreamResp.status}`);
    return new Response(`Upstream returned ${upstreamResp.status}`, { status: 502 });
  }

  const upstream = upstreamResp.webSocket;
  if (!upstream) {
    return new Response("No WebSocket in upstream response", { status: 502 });
  }

  const { 0: client, 1: server } = new WebSocketPair();
  upstream.accept();
  server.accept();

  server.addEventListener("message", ({ data }) => {
    try { upstream.send(data); } catch (e) { console.error("ds client→upstream:", e.message); }
  });
  upstream.addEventListener("message", ({ data }) => {
    try { server.send(data); } catch (e) { console.error("ds upstream→client:", e.message); }
  });
  server.addEventListener("close",   ({ code, reason }) => { try { upstream.close(code, reason); } catch {} });
  upstream.addEventListener("close", ({ code, reason }) => { try { server.close(code, reason);   } catch {} });

  return new Response(null, { status: 101, webSocket: client });
}

// ---------------------------------------------------------------------------
// Route: POST /rest-proxy  -- REST passthrough for Axiom discovery endpoints
// Body: { secret, path, cookie?, server? }
//
// Axiom's Cloudflare WAF returns 526/502 for Railway datacenter IPs on REST.
// This Worker runs on Cloudflare's edge — seen as another CF node and allowed
// through. Returns the upstream JSON body with the upstream status code.
// ---------------------------------------------------------------------------
async function handleRestProxy(request, env, secret) {
  if (request.method !== "POST") {
    return json({ ok: false, error: "POST only" }, 405);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "Invalid JSON" }, 400);
  }

  if (!body.secret || body.secret !== secret) {
    return json({ ok: false, error: "Unauthorized" }, 401);
  }

  const path   = String(body.path || "").trim();
  const cookie = String(body.cookie || "").trim();
  if (!path || !path.startsWith("/")) {
    return json({ ok: false, error: "path must start with /" }, 400);
  }

  const allowedServers = new Set(AXIOM_SERVERS);
  const server = allowedServers.has(body.server) ? body.server : "https://api3.axiom.trade";

  const upstreamUrl = `${server}${path}`;
  const headers = { ...BROWSER_HEADERS };
  if (cookie) headers["Cookie"] = cookie;

  try {
    const resp = await fetch(upstreamUrl, { method: "GET", headers });
    const text = await resp.text();
    console.log(`rest-proxy ${server}${path} -> ${resp.status} (${text.length}b)`);
    return new Response(text, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/json",
        "X-Upstream-Server": server,
      },
    });
  } catch (e) {
    console.error(`rest-proxy ${upstreamUrl} error: ${e.message}`);
    return json({ ok: false, error: `Upstream fetch failed: ${e.message}` }, 502);
  }
}

// ---------------------------------------------------------------------------
function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
