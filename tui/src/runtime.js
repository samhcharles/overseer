export function shortEndpoint(url) {
  if (!url) return "-";
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function shortModel(model) {
  if (!model) return null;
  const normalized = String(model).trim();
  if (!normalized) return null;
  if (!normalized.includes("/")) return normalized;
  const parts = normalized.split("/");
  return parts[parts.length - 1];
}

export function displayPath(cwd) {
  if (!cwd) return ".";
  const normalized = String(cwd).replace(/\\/g, "/");
  const home = process.env.HOME?.replace(/\\/g, "/");
  if (home && normalized.startsWith(home)) {
    return `~${normalized.slice(home.length) || "/"}`;
  }
  return normalized;
}

export function cleanRuntimeValue(value) {
  if (!value) return null;
  const normalized = String(value).trim();
  if (!normalized || normalized === "none" || normalized === "-") return null;
  return normalized;
}

export function configuredModelFromHealth(health) {
  const providerConfig = health?.provider_config ?? {};
  const backend = cleanRuntimeValue(health?.backend);
  if (!backend || backend === "rotator" || backend === "auto") return null;
  if (backend === "node") {
    return shortModel(health?.trusted_nodes?.[0]?.models?.[0]);
  }
  if (backend === "openrouter") {
    return shortModel(providerConfig.openrouter?.model);
  }
  if (backend === "ollama") {
    return shortModel(providerConfig.ollama?.model);
  }
  if (backend === "gemini") {
    return shortModel(providerConfig.gemini?.model);
  }
  if (backend === "groq") {
    return shortModel(providerConfig.groq?.model);
  }
  return null;
}

export function currentModelLabel(lastModel, health) {
  return shortModel(cleanRuntimeValue(lastModel))
    ?? shortModel(health?.trusted_nodes?.[0]?.models?.[0])
    ?? configuredModelFromHealth(health)
    ?? "-";
}

export function formatRuntimeDescriptor(runtime, model) {
  if (!runtime || runtime === "-") return model && model !== "-" ? model : "-";
  if (!model || model === "-" || model === runtime) return runtime;
  return `${runtime} · ${model}`;
}
