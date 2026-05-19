/**
 * Overseer Worker — Cloudflare edge tools for Overseer.
 *
 * Routes (all require Authorization: Bearer $WORKER_SECRET):
 *   GET  /fetch?url=...          — fetch URL, return {url, status, content}
 *   GET  /kv/get?key=...         — read from Workers KV
 *   POST /kv/set                 — write to Workers KV  {key, value, ttl?}
 *   POST /webhook/:source        — forward to VPS /extract
 *   Cron trigger                 — daily digest → VPS /chat
 */

function unauthorized() {
  return new Response(JSON.stringify({ error: "unauthorized" }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function checkAuth(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${env.WORKER_SECRET}`;
}

// ── /fetch ────────────────────────────────────────────────────────────────────

async function handleFetch(request, env) {
  const url = new URL(request.url).searchParams.get("url");
  if (!url) return json({ error: "url param required" }, 400);

  try {
    const r = await fetch(url, {
      headers: { "User-Agent": "Overseer/1.0 (+https://github.com/samhcharles/overseer)" },
      redirect: "follow",
      cf: { cacheEverything: false },
    });
    const contentType = r.headers.get("content-type") || "";
    let content = await r.text();

    // Strip HTML tags for cleaner content
    if (contentType.includes("text/html")) {
      content = content
        .replace(/<script[\s\S]*?<\/script>/gi, "")
        .replace(/<style[\s\S]*?<\/style>/gi, "")
        .replace(/<[^>]+>/g, " ")
        .replace(/\s{2,}/g, " ")
        .trim()
        .slice(0, 8000);
    } else {
      content = content.slice(0, 8000);
    }

    return json({ url, status: r.status, content });
  } catch (e) {
    return json({ error: e.message, url }, 502);
  }
}

// ── /kv/get ───────────────────────────────────────────────────────────────────

async function handleKvGet(request, env) {
  const key = new URL(request.url).searchParams.get("key");
  if (!key) return json({ error: "key param required" }, 400);
  const value = await env.OVERSEER_KV.get(key);
  return json({ key, value, found: value !== null });
}

// ── /kv/set ───────────────────────────────────────────────────────────────────

async function handleKvSet(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { key, value, ttl } = body;
  if (!key || value === undefined) return json({ error: "key and value required" }, 400);
  const opts = ttl ? { expirationTtl: Number(ttl) } : {};
  await env.OVERSEER_KV.put(key, String(value), opts);
  return json({ key, stored: true });
}

// ── /webhook/:source ──────────────────────────────────────────────────────────

function overseerHeaders(env) {
  const h = { "Content-Type": "application/json" };
  if (env.OVERSEER_API_KEY) h["Authorization"] = `Bearer ${env.OVERSEER_API_KEY}`;
  return h;
}

async function handleWebhook(request, env, source) {
  let body;
  try {
    body = await request.text();
  } catch {
    body = "";
  }

  const overseerUrl = env.OVERSEER_API_URL || "https://overseer.wokspec.org";
  let text = body;

  try {
    const parsed = JSON.parse(body);
    if (source === "github" && parsed.commits) {
      const msgs = parsed.commits.map((c) => `${c.id.slice(0, 7)} ${c.message}`).join("\n");
      text = `GitHub push to ${parsed.repository?.name}: \n${msgs}`;
    } else if (parsed.text) {
      text = parsed.text;
    }
  } catch {}

  try {
    const r = await fetch(`${overseerUrl}/extract`, {
      method: "POST",
      headers: overseerHeaders(env),
      body: JSON.stringify({ text, session_id: `webhook-${source}` }),
    });
    const result = await r.json();
    return json({ source, forwarded: true, vault_writes: result.vault_writes ?? [] });
  } catch (e) {
    return json({ source, forwarded: false, error: e.message }, 502);
  }
}

// ── cron ──────────────────────────────────────────────────────────────────────

async function handleCron(env) {
  const overseerUrl = env.OVERSEER_API_URL || "https://overseer.wokspec.org";
  const now = new Date().toLocaleString("en-US", { timeZone: "America/Los_Angeles" });
  const prompt = `Daily digest — ${now}. Check my vault for anything overdue, upcoming, or worth surfacing today. Keep it under 5 bullets.`;

  try {
    const r = await fetch(`${overseerUrl}/chat`, {
      method: "POST",
      headers: overseerHeaders(env),
      body: JSON.stringify({ message: prompt }),
    });
    const result = await r.json();
    console.log("Cron digest:", result.response?.slice(0, 200));
    return result;
  } catch (e) {
    console.error("Cron failed:", e.message);
  }
}

// ── router ────────────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (!checkAuth(request, env)) return unauthorized();

    if (request.method === "GET" && path === "/fetch") return handleFetch(request, env);
    if (request.method === "GET" && path === "/kv/get") return handleKvGet(request, env);
    if (request.method === "POST" && path === "/kv/set") return handleKvSet(request, env);
    if (request.method === "POST" && path.startsWith("/webhook/")) {
      const source = path.replace("/webhook/", "") || "unknown";
      return handleWebhook(request, env, source);
    }
    if (path === "/health") return json({ status: "ok" });

    return json({ error: "not found", path }, 404);
  },

  async scheduled(event, env) {
    await handleCron(env);
  },
};
