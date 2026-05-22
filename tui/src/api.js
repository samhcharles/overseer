import { API_KEY, API_SOURCE, API_URL, API_URLS } from "./config.js";

let resolvedApiUrl = null;

class ApiRequestError extends Error {
  constructor(message, { attempts = [], cause = null } = {}) {
    super(message);
    this.name = "ApiRequestError";
    this.attempts = attempts;
    this.cause = cause;
  }
}

function authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (API_KEY) h["Authorization"] = `Bearer ${API_KEY}`;
  return h;
}

function describeError(error) {
  if (error instanceof ApiRequestError) return error.message;
  if (error?.name === "AbortError") return "request aborted";
  if (error?.message) return error.message;
  return "request failed";
}

function buildApiError(message, attempts, cause = null) {
  return new ApiRequestError(message, { attempts, cause });
}

async function requestJson(path, { signal, baseUrl, method = "GET", body } = {}) {
  const r = await fetch(`${baseUrl}${path}`, {
    method,
    headers: authHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!r.ok) throw new Error(`http ${r.status}`);
  return r.json();
}

async function resolveApiUrl(signal) {
  if (resolvedApiUrl) return resolvedApiUrl;
  const health = await fetchHealth(signal);
  return health.api_url;
}

function withApiMetadata(data, apiUrl) {
  return { ...data, api_url: apiUrl, api_urls: API_URLS, api_source: API_SOURCE };
}

async function requestResolvedApi(path, { signal, apiUrlOverride, method = "GET", body, errorPrefix = "unable to reach Overseer" } = {}) {
  const apiUrl = apiUrlOverride || await resolveApiUrl(signal);
  try {
    const data = await requestJson(path, {
      method,
      body,
      signal,
      baseUrl: apiUrl,
    });
    resolvedApiUrl = apiUrl;
    return withApiMetadata(data, apiUrl);
  } catch (error) {
    if (error?.name !== "AbortError") resolvedApiUrl = null;
    throw buildApiError(
      `${errorPrefix} at ${apiUrl}: ${describeError(error)}`,
      [{ url: apiUrl, error: describeError(error) }],
      error,
    );
  }
}

export async function fetchHealth(signal) {
  const attempts = [];
  for (const apiUrl of API_URLS) {
    try {
      const data = await requestJson("/health", { signal, baseUrl: apiUrl });
      resolvedApiUrl = apiUrl;
      return withApiMetadata(data, apiUrl);
    } catch (error) {
      attempts.push({ url: apiUrl, error: describeError(error) });
    }
  }
  throw buildApiError(`unable to reach Overseer at ${API_URLS.join(", ")}`, attempts);
}

async function sendJson(path, body, signal, apiUrlOverride) {
  return requestResolvedApi(path, {
    method: "POST",
    body,
    signal,
    apiUrlOverride,
    errorPrefix: "unable to reach Overseer",
  });
}

export async function sendChat(message, mode, signal, apiUrlOverride) {
  return sendJson("/chat", { message, mode }, signal, apiUrlOverride);
}

export async function extractText(text, sessionId, mode, signal, apiUrlOverride) {
  return sendJson("/extract", { text, session_id: sessionId, mode }, signal, apiUrlOverride);
}

export async function fetchProviders(signal, apiUrlOverride) {
  return requestResolvedApi("/providers", {
    signal,
    apiUrlOverride,
    errorPrefix: "unable to reach Overseer providers",
  });
}

export async function updateProviders(patch, signal, apiUrlOverride) {
  return requestResolvedApi("/providers", {
    method: "PATCH",
    body: patch,
    signal,
    apiUrlOverride,
    errorPrefix: "unable to update Overseer providers",
  });
}

export function getApiRuntime() {
  return {
    configuredUrls: API_URLS,
    defaultUrl: API_URL,
    resolvedUrl: resolvedApiUrl,
    source: API_SOURCE,
  };
}

export function formatApiError(error) {
  if (error?.attempts?.length) {
    return error.attempts.map(({ url, error: detail }) => `${url} -> ${detail}`).join("  |  ");
  }
  return describeError(error);
}
