/**
 * Overseer Worker — Cloudflare edge tools for Overseer.
 *
 * Overseer calls this worker as a tool. This worker does NOT call back to Overseer.
 *
 * Routes (all require Authorization: Bearer $WORKER_SECRET):
 *   GET  /fetch?url=...          — fetch URL, return {url, status, content}
 *   GET  /kv/get?key=...         — read from Workers KV
 *   POST /kv/set                 — write to Workers KV  {key, value, ttl?}
 *   POST /webhook/:source        — store payload in KV as pending:webhook:{source}:{ts}
 *   Cron trigger                 — write pending:cron:{date} to KV for Overseer to pick up
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
      headers: { "User-Agent": "Overseer/1.0" },
      redirect: "follow",
      cf: { cacheEverything: false },
    });
    const contentType = r.headers.get("content-type") || "";
    let content = await r.text();

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
// Store incoming payload in KV for Overseer to pull when it's ready.

async function handleWebhook(request, env, source) {
  let body;
  try {
    body = await request.text();
  } catch {
    body = "";
  }

  let text = body;
  try {
    const parsed = JSON.parse(body);
    if (source === "github" && parsed.commits) {
      const msgs = parsed.commits.map((c) => `${c.id.slice(0, 7)} ${c.message}`).join("\n");
      text = `GitHub push to ${parsed.repository?.name}:\n${msgs}`;
    } else if (parsed.text) {
      text = parsed.text;
    }
  } catch {}

  const ts = Date.now();
  const key = `pending:webhook:${source}:${ts}`;
  await env.OVERSEER_KV.put(key, text, { expirationTtl: 86400 });

  return json({ source, queued: true, key });
}

// ── cron ──────────────────────────────────────────────────────────────────────
// Write a pending digest marker to KV. Overseer polls for it on next run.

async function handleCron(env) {
  const date = new Date().toISOString().slice(0, 10);
  const key = `pending:cron:${date}`;
  await env.OVERSEER_KV.put(key, date, { expirationTtl: 86400 });
  console.log("Cron: queued digest for", date);
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
