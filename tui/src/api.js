import { API_KEY, API_URL } from "./config.js";

function authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (API_KEY) h["Authorization"] = `Bearer ${API_KEY}`;
  return h;
}

export async function fetchHealth(signal) {
  const r = await fetch(`${API_URL}/health`, { signal });
  if (!r.ok) throw new Error(`http ${r.status}`);
  return r.json();
}

export async function sendChat(message, signal) {
  const r = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ message }),
    signal,
  });
  if (!r.ok) throw new Error(`http ${r.status}`);
  return r.json();
}
