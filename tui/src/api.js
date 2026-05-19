import { API_URL } from "./config.js";

export async function fetchHealth(signal) {
  const r = await fetch(`${API_URL}/health`, { signal });
  if (!r.ok) throw new Error(`http ${r.status}`);
  return r.json();
}

export async function sendChat(message, signal) {
  const r = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!r.ok) throw new Error(`http ${r.status}`);
  return r.json();
}
