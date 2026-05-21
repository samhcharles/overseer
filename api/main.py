"""
Overseer API — vault-connected AI gateway.
Backends rotate automatically across all configured free providers.
No single primary — each request goes to the next available slot.

Provider pool (in default rotation order):
  OpenRouter :free models (3 slots) → Gemini → Groq → Ollama (local)

OVERSEER_BACKEND: set to a provider name to pin it (debug only).
  Unset or "auto" → rotation across all configured providers.
"""
import asyncio
import functools
import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/home/ubuntu/vault"))
FOUNDER_URL = os.environ.get("FOUNDER_URL", "").rstrip("/")

# Provider secrets stay env-backed. Editable runtime settings live in runtime-config.json.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE = "https://api.groq.com/openai/v1"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

WORKER_URL = os.environ.get("WORKER_URL", "")
WORKER_SECRET = os.environ.get("WORKER_SECRET", "")

OVERSEER_API_KEY = os.environ.get("OVERSEER_API_KEY", "")

USER_TIMEZONE = os.environ.get("USER_TIMEZONE", "America/Los_Angeles")
OVERSEER_NODE_SECRET = os.environ.get("OVERSEER_NODE_SECRET", "")
OVERSEER_ALLOWED_NODE_IDS = {v.strip() for v in os.environ.get("OVERSEER_ALLOWED_NODE_IDS", "").split(",") if v.strip()}
OVERSEER_OWNER_ID = os.environ.get("OVERSEER_OWNER_ID", "").strip()
OVERSEER_NODE_TTL = int(os.environ.get("OVERSEER_NODE_TTL", "90"))
OVERSEER_REQUIRE_TAILNET = os.environ.get("OVERSEER_REQUIRE_TAILNET", "true").lower() not in {"0", "false", "no"}
DEFAULT_OVERSEER_BACKEND = os.environ.get("OVERSEER_BACKEND", "auto")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
DEFAULT_OPENROUTER_MODELS_RAW = os.environ.get(
    "OPENROUTER_MODELS",
    "google/gemini-2.0-flash-exp:free,meta-llama/llama-3.3-70b-instruct:free,deepseek/deepseek-chat-v3-0324:free",
)
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
DEFAULT_PREFER_LOCAL_NODES = os.environ.get("OVERSEER_PREFER_LOCAL_NODES", "true").lower() not in {"0", "false", "no"}
VALID_BACKENDS = {"auto", "gemini", "groq", "openrouter", "ollama", "node"}
RUNTIME_CONFIG_VERSION = 1
DEFAULT_CONFIG_DIR = Path("/config") if Path("/config").is_dir() else Path.home() / ".local" / "state" / "overseer"
OVERSEER_CONFIG_DIR = Path(os.environ.get("OVERSEER_CONFIG_DIR", str(DEFAULT_CONFIG_DIR)))
RUNTIME_CONFIG_PATH = OVERSEER_CONFIG_DIR / "runtime-config.json"


def _clean_text(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    cleaned = str(value).strip()
    return cleaned or fallback


def _clean_url(value: object, fallback: str = "") -> str:
    return _clean_text(value, fallback).rstrip("/")


def _bool_value(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return fallback


def _parse_model_list(value: object, *, require_free: bool = False) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    models: list[str] = []
    seen: set[str] = set()
    for item in items:
        model = _clean_text(item)
        if not model:
            continue
        if require_free and not model.endswith(":free"):
            continue
        if model in seen:
            continue
        seen.add(model)
        models.append(model)
    return models


def _runtime_defaults() -> dict:
    return {
        "version": RUNTIME_CONFIG_VERSION,
        "backend": _clean_text(DEFAULT_OVERSEER_BACKEND, "auto"),
        "prefer_local_nodes": DEFAULT_PREFER_LOCAL_NODES,
        "providers": {
            "gemini": {
                "enabled": bool(GEMINI_API_KEY),
                "model": _clean_text(DEFAULT_GEMINI_MODEL, "gemini-2.0-flash"),
            },
            "groq": {
                "enabled": bool(GROQ_API_KEY),
                "model": _clean_text(DEFAULT_GROQ_MODEL, "llama-3.3-70b-versatile"),
            },
            "openrouter": {
                "enabled": bool(OPENROUTER_API_KEY),
                "model": _clean_text(DEFAULT_OPENROUTER_MODEL, "google/gemini-2.0-flash-exp:free"),
                "models": _parse_model_list(DEFAULT_OPENROUTER_MODELS_RAW, require_free=True),
            },
            "ollama": {
                "enabled": bool(_clean_url(DEFAULT_OLLAMA_URL, "http://localhost:11434")),
                "url": _clean_url(DEFAULT_OLLAMA_URL, "http://localhost:11434"),
                "model": _clean_text(DEFAULT_OLLAMA_MODEL, "qwen2.5:14b"),
            },
        },
    }


def _normalize_runtime_config(raw: dict | None) -> dict:
    defaults = _runtime_defaults()
    cfg = deepcopy(defaults)
    if isinstance(raw, dict):
        backend = _clean_text(raw.get("backend"), defaults["backend"])
        cfg["backend"] = backend if backend in VALID_BACKENDS else "auto"
        cfg["prefer_local_nodes"] = _bool_value(raw.get("prefer_local_nodes"), defaults["prefer_local_nodes"])

        providers_raw = raw.get("providers")
        if isinstance(providers_raw, dict):
            for provider_name in cfg["providers"]:
                current = cfg["providers"][provider_name]
                entry = providers_raw.get(provider_name)
                if not isinstance(entry, dict):
                    continue
                current["enabled"] = _bool_value(entry.get("enabled"), current["enabled"])
                if provider_name == "openrouter":
                    current["model"] = _clean_text(entry.get("model"), current["model"])
                    current["models"] = _parse_model_list(entry.get("models"), require_free=True) or current["models"]
                    if current["model"].endswith(":free") and current["model"] not in current["models"]:
                        current["models"].insert(0, current["model"])
                    if current["models"] and current["model"] not in current["models"]:
                        current["model"] = current["models"][0]
                elif provider_name == "ollama":
                    current["url"] = _clean_url(entry.get("url"), current["url"])
                    current["model"] = _clean_text(entry.get("model"), current["model"])
                else:
                    current["model"] = _clean_text(entry.get("model"), current["model"])

    if cfg["backend"] == "openrouter":
        models = cfg["providers"]["openrouter"]["models"]
        if not models:
            cfg["backend"] = "auto"
        elif cfg["providers"]["openrouter"]["model"] not in models:
            cfg["providers"]["openrouter"]["model"] = models[0]
    if cfg["backend"] == "ollama" and not cfg["providers"]["ollama"]["url"]:
        cfg["backend"] = "auto"
    return cfg


def _load_runtime_config() -> dict:
    if not RUNTIME_CONFIG_PATH.exists():
        return _normalize_runtime_config(None)
    try:
        raw = json.loads(RUNTIME_CONFIG_PATH.read_text())
    except Exception:
        logging.warning("overseer: failed to read runtime config at %s, falling back to defaults", RUNTIME_CONFIG_PATH)
        return _normalize_runtime_config(None)
    return _normalize_runtime_config(raw)


_runtime_config = _load_runtime_config()
_config_lock = threading.Lock()

app = FastAPI(title="Overseer API", version="1.0.0")

_bearer = HTTPBearer(auto_error=False)


# ─── provider rotation ────────────────────────────────────────────────────────

class RateLimitError(Exception):
    pass


@dataclass
class _Slot:
    name: str
    call: Callable
    reset_ttl: int = 3600
    blocked_until: float = field(default=0.0, init=False)


@dataclass
class NodeRecord:
    node_id: str
    hostname: str
    tailscale_ip: str
    inference_url: str
    scope: str
    owner: str | None
    models: list[str]
    capabilities: list[str]
    version: str | None
    last_seen_monotonic: float = field(default_factory=time.monotonic)
    last_seen_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def touch(self, *, models: list[str] | None = None, capabilities: list[str] | None = None) -> None:
        if models is not None:
            self.models = models
        if capabilities is not None:
            self.capabilities = capabilities
        self.last_seen_monotonic = time.monotonic()
        self.last_seen_utc = datetime.now(timezone.utc).isoformat()

    def public_dict(self) -> dict:
        age_seconds = max(0, int(time.monotonic() - self.last_seen_monotonic))
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "tailscale_ip": self.tailscale_ip,
            "inference_url": self.inference_url,
            "scope": self.scope,
            "owner": self.owner,
            "models": self.models,
            "capabilities": self.capabilities,
            "version": self.version,
            "last_seen_utc": self.last_seen_utc,
            "age_seconds": age_seconds,
        }


class ProviderRotator:
    def __init__(self, slots: list[_Slot]):
        self._slots = slots
        self._lock = threading.Lock()
        self._idx = 0

    def next(self) -> "_Slot | None":
        now = time.monotonic()
        with self._lock:
            for _ in range(len(self._slots)):
                slot = self._slots[self._idx % len(self._slots)]
                self._idx += 1
                if slot.blocked_until <= now:
                    return slot
        return None

    def block(self, slot: _Slot) -> None:
        slot.blocked_until = time.monotonic() + slot.reset_ttl
        logging.warning("overseer: blocked slot %s for %ds", slot.name, slot.reset_ttl)

    def soonest_reset(self) -> float:
        return min((s.blocked_until for s in self._slots), default=0.0)

    def status(self) -> tuple[list[str], dict[str, str]]:
        now = time.monotonic()
        active = [s.name for s in self._slots if s.blocked_until <= now]
        blocked = {
            s.name: f"resets in {max(0, int(s.blocked_until - now)) // 60}m"
            for s in self._slots if s.blocked_until > now
        }
        return active, blocked


# _rotator is initialized after the _*_chat functions are defined (see below).
_rotator: ProviderRotator | None = None


# Simple in-process rate limiter: max N requests per window per IP
_rate_store: dict[str, list[float]] = {}
_RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "60"))   # requests
_RATE_WINDOW = int(os.environ.get("RATE_WINDOW", "60"))  # seconds


def _auth(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    # Auth check
    if OVERSEER_API_KEY:
        if not credentials or credentials.credentials != OVERSEER_API_KEY:
            raise HTTPException(status_code=401, detail="unauthorized")

    # Rate limit by IP (uses CF-Connecting-IP header when behind Cloudflare Tunnel)
    ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or "unknown"
    now = time.monotonic()
    window_start = now - _RATE_WINDOW
    hits = _rate_store.get(ip, [])
    hits = [t for t in hits if t > window_start]
    if len(hits) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail=f"rate limit: {_RATE_LIMIT} req/{_RATE_WINDOW}s")
    hits.append(now)
    _rate_store[ip] = hits


def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client:
        return request.client.host
    return "unknown"


def _is_private_or_tailscale(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_private:
        return True
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return True
    if ip.version == 6 and ip in ipaddress.ip_network("fc00::/7"):
        return True
    return False


def _prune_nodes() -> None:
    now = time.monotonic()
    with _node_lock:
        stale = [node_id for node_id, record in _node_registry.items() if now - record.last_seen_monotonic > OVERSEER_NODE_TTL]
        for node_id in stale:
            _node_registry.pop(node_id, None)


def _trusted_nodes() -> list[NodeRecord]:
    _prune_nodes()
    with _node_lock:
        return sorted(_node_registry.values(), key=lambda record: record.last_seen_monotonic, reverse=True)


def _validate_node_network(tailscale_ip: str, inference_url: str) -> None:
    if not _is_private_or_tailscale(tailscale_ip):
        raise HTTPException(status_code=403, detail=f"tailscale_ip must be private or tailscale, got {tailscale_ip}")
    host = urlparse(inference_url).hostname or ""
    if not host or not _is_private_or_tailscale(host):
        raise HTTPException(status_code=403, detail=f"inference_url must target a private or tailscale host, got {inference_url}")


def _node_access(request: Request, node_id: str, secret: str, owner: str | None = None) -> str:
    if not OVERSEER_NODE_SECRET:
        raise HTTPException(status_code=503, detail="node registry disabled")
    if secret != OVERSEER_NODE_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized node")
    client_ip = _request_ip(request)
    if OVERSEER_REQUIRE_TAILNET and not _is_private_or_tailscale(client_ip):
        raise HTTPException(status_code=403, detail=f"node access requires private or tailscale ip, got {client_ip}")
    if OVERSEER_ALLOWED_NODE_IDS and node_id not in OVERSEER_ALLOWED_NODE_IDS:
        raise HTTPException(status_code=403, detail=f"node {node_id} is not allowlisted")
    if OVERSEER_OWNER_ID and owner and owner != OVERSEER_OWNER_ID:
        raise HTTPException(status_code=403, detail="owner mismatch")
    return client_ip

token_ledger: dict[str, int] = {}

_system_prompt_cache: dict[str, tuple[float, str]] = {}
_SYSTEM_PROMPT_TTL = 300  # 5 minutes
_health_probe_cache: tuple[float, dict] | None = None
_HEALTH_PROBE_TTL = 30
_node_registry: dict[str, NodeRecord] = {}
_node_lock = threading.Lock()

_SKIP_EXTRACTION_RE = re.compile(
    r"^\s*(hi|hello|hey|ok|k|thanks|thx|sure|yes|no|yep|nope|cool|got it|lol|haha|bye|done|nice)\W*$",
    re.IGNORECASE,
)


def _config_copy() -> dict:
    with _config_lock:
        return deepcopy(_runtime_config)


def _persist_runtime_config(cfg: dict) -> None:
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = RUNTIME_CONFIG_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    tmp_path.replace(RUNTIME_CONFIG_PATH)


def _available_backend_names(cfg: dict | None = None) -> list[str]:
    config = cfg or _config_copy()
    names = ["auto"]
    if OVERSEER_NODE_SECRET:
        names.append("node")
    providers = config["providers"]
    if providers["openrouter"]["enabled"] and OPENROUTER_API_KEY and providers["openrouter"]["models"]:
        names.append("openrouter")
    if providers["gemini"]["enabled"] and GEMINI_API_KEY:
        names.append("gemini")
    if providers["groq"]["enabled"] and GROQ_API_KEY:
        names.append("groq")
    if providers["ollama"]["enabled"] and providers["ollama"]["url"]:
        names.append("ollama")
    return names


async def _ollama_models(ollama_url: str) -> list[str]:
    target = _clean_url(ollama_url)
    if not target:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{target}/api/tags")
            response.raise_for_status()
            payload = response.json()
        return [item.get("name") for item in payload.get("models", []) if item.get("name")]
    except Exception:
        return []


def _update_runtime_config(changes: dict) -> dict:
    global _runtime_config, _health_probe_cache
    with _config_lock:
        next_cfg = deepcopy(_runtime_config)
        next_cfg["version"] = RUNTIME_CONFIG_VERSION
        next_cfg["backend"] = changes.get("backend", next_cfg["backend"])
        next_cfg["prefer_local_nodes"] = changes.get("prefer_local_nodes", next_cfg["prefer_local_nodes"])
        providers = next_cfg["providers"]
        for provider_name in ("gemini", "groq", "openrouter", "ollama"):
            provider_changes = changes.get("providers", {}).get(provider_name, {})
            if not provider_changes:
                continue
            providers[provider_name].update(provider_changes)
        normalized = _normalize_runtime_config(next_cfg)
        _persist_runtime_config(normalized)
        _runtime_config = normalized
        _health_probe_cache = None
    return deepcopy(normalized)


# ─── vault tools ──────────────────────────────────────────────────────────────

def vault_read(path: str) -> str:
    full = VAULT_PATH / path.lstrip("/")
    if not full.exists():
        return f"[not found: {path}]"
    if full.is_dir():
        entries = sorted(p.name for p in full.iterdir())
        if not entries:
            return f"[directory empty: {path}]"
        return "\n".join(f"{path.rstrip('/')}/{name}" for name in entries[:100])
    return full.read_text()


def vault_write(path: str, content: str, commit_msg: str | None = None) -> str:
    full = VAULT_PATH / path.lstrip("/")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    msg = commit_msg or f"overseer: update {path}"
    try:
        git = ["git", "-C", str(VAULT_PATH)]
        subprocess.run(git + ["config", "--global", "--add", "safe.directory", str(VAULT_PATH)], capture_output=True)
        subprocess.run(git + ["config", "user.email", "overseer@brain"], capture_output=True)
        subprocess.run(git + ["config", "user.name", "Overseer"], capture_output=True)
        subprocess.run(git + ["add", str(full)], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-m", msg], check=True, capture_output=True)
        subprocess.run(git + ["pull", "--rebase", "origin", "main"], check=True, capture_output=True)
        subprocess.run(git + ["push", "origin", "main"], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"vault git error: {e.stderr.decode()[:200]}")
    return f"wrote and committed {path}"


def vault_search(query: str, max_results: int = 10) -> str:
    result = subprocess.run(
        ["rg", "--ignore-case", "--max-count=3", "--with-filename", query, str(VAULT_PATH)],
        capture_output=True, text=True
    )
    lines = result.stdout.strip().splitlines()[:max_results]
    return "\n".join(lines) if lines else "[no results]"


def list_notes(folder: str) -> str:
    full = VAULT_PATH / folder.lstrip("/")
    if not full.is_dir():
        return f"[not a directory: {folder}]"
    files = [str(p.relative_to(VAULT_PATH)) for p in sorted(full.rglob("*.md"))]
    return "\n".join(files) if files else "[empty]"


def web_search(query: str) -> str:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        lines = [f"**{r.get('title','')}**\n{r.get('body','')}\n{r.get('href','')}" for r in results]
        return "\n\n".join(lines) if lines else "[no results]"
    except Exception as e:
        return f"[search error: {e}]"


def _worker_headers() -> dict:
    return {"Authorization": f"Bearer {WORKER_SECRET}", "Content-Type": "application/json"}


def web_fetch(url: str) -> str:
    if not WORKER_URL:
        return "[worker not configured: WORKER_URL not set]"
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{WORKER_URL}/fetch?url={url}",
            headers=_worker_headers(),
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            import json as _json
            data = _json.loads(r.read())
        return data.get("content") or f"[empty response from {url}]"
    except Exception as e:
        return f"[fetch error: {e}]"


def kv_get(key: str) -> str:
    if not WORKER_URL:
        return "[worker not configured]"
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{WORKER_URL}/kv/get?key={key}",
            headers=_worker_headers(),
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            import json as _json
            data = _json.loads(r.read())
        return data.get("value") if data.get("found") else f"[not found: {key}]"
    except Exception as e:
        return f"[kv_get error: {e}]"


def kv_set(key: str, value: str, ttl: int = 86400) -> str:
    if not WORKER_URL:
        return "[worker not configured]"
    try:
        import urllib.request, json as _json
        body = _json.dumps({"key": key, "value": value, "ttl": ttl}).encode()
        req = urllib.request.Request(
            f"{WORKER_URL}/kv/set",
            data=body,
            headers=_worker_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        return f"stored {key}" if data.get("stored") else f"[kv_set failed]"
    except Exception as e:
        return f"[kv_set error: {e}]"


async def _probe_url(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> str:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
        return "ok" if response.is_success else f"http {response.status_code}"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


async def _health_probes() -> dict:
    global _health_probe_cache

    now = time.monotonic()
    if _health_probe_cache and now - _health_probe_cache[0] < _HEALTH_PROBE_TTL:
        return _health_probe_cache[1]

    cfg = _config_copy()
    providers = cfg["providers"]
    probe_jobs: dict[str, asyncio.Future] = {}
    provider_statuses: dict[str, str] = {}

    if providers["openrouter"]["enabled"] and OPENROUTER_API_KEY and providers["openrouter"]["models"]:
        probe_jobs["openrouter"] = asyncio.create_task(
            _probe_url(
                f"{OPENROUTER_BASE}/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            )
        )
    else:
        provider_statuses["openrouter"] = "disabled" if providers["openrouter"]["enabled"] else "muted"

    if providers["gemini"]["enabled"] and GEMINI_API_KEY:
        probe_jobs["gemini"] = asyncio.create_task(
            _probe_url(
                "https://generativelanguage.googleapis.com/v1beta/models",
                timeout=5.0,
                headers=None,
            )
        )
    else:
        provider_statuses["gemini"] = "disabled" if providers["gemini"]["enabled"] else "muted"

    if providers["groq"]["enabled"] and GROQ_API_KEY:
        probe_jobs["groq"] = asyncio.create_task(
            _probe_url(
                f"{GROQ_BASE}/models",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
        )
    else:
        provider_statuses["groq"] = "disabled" if providers["groq"]["enabled"] else "muted"

    if providers["ollama"]["enabled"] and providers["ollama"]["url"]:
        probe_jobs["ollama"] = asyncio.create_task(_probe_url(f"{providers['ollama']['url']}/api/tags", timeout=5.0))
    else:
        provider_statuses["ollama"] = "disabled" if providers["ollama"]["enabled"] else "muted"

    worker_status = "disabled"
    if WORKER_URL:
        worker_status = await _probe_url(
            f"{WORKER_URL}/health",
            headers={"Authorization": f"Bearer {WORKER_SECRET}"} if WORKER_SECRET else None,
            timeout=5.0,
        )

    if probe_jobs:
        results = await asyncio.gather(*probe_jobs.values(), return_exceptions=True)
        for name, result in zip(probe_jobs.keys(), results):
            if isinstance(result, Exception):
                provider_statuses[name] = f"error: {type(result).__name__}"
            else:
                provider_statuses[name] = result

    enabled_statuses = [status for status in provider_statuses.values() if status not in {"disabled", "muted"}]
    backend_status = "ok" if any(status == "ok" for status in enabled_statuses) else (
        enabled_statuses[0] if enabled_statuses else "no providers configured"
    )

    data = {
        "provider_statuses": provider_statuses,
        "worker_status": worker_status,
        "backend_status": backend_status,
    }
    _health_probe_cache = (now, data)
    return data


TOOLS_MAP = {
    "vault_read": vault_read,
    "vault_write": vault_write,
    "vault_search": vault_search,
    "list_notes": list_notes,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "kv_get": kv_get,
    "kv_set": kv_set,
}

WRITE_TOOL_NAMES = {"vault_write", "kv_set"}
READ_ONLY_TOOLS_MAP = {
    name: tool for name, tool in TOOLS_MAP.items() if name not in WRITE_TOOL_NAMES
}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "vault_read",
        "description": "Read a file from the vault. Path relative to vault root.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "vault_write",
        "description": "Write content to a vault file and commit+push.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    }},
    {"type": "function", "function": {
        "name": "vault_search",
        "description": "Search vault using ripgrep. Returns matching lines with file paths.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "list_notes",
        "description": "List all markdown files in a vault folder.",
        "parameters": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web via DuckDuckGo. Returns top 5 results.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch the full content of a URL via Cloudflare edge worker. Better than web_search for reading actual page content.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "kv_get",
        "description": "Read a value from Overseer's edge KV store by key.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    }},
    {"type": "function", "function": {
        "name": "kv_set",
        "description": "Write a value to Overseer's edge KV store. Defaults to 24h TTL.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "ttl": {"type": "integer", "description": "TTL in seconds, default 86400"},
            },
            "required": ["key", "value"],
        },
    }},
]

READ_ONLY_TOOLS_SPEC = [
    spec for spec in TOOLS_SPEC if spec.get("function", {}).get("name") not in WRITE_TOOL_NAMES
]


def _tool_contract_for_mode(mode: Literal["chat", "think", "capture"]) -> tuple[dict[str, Callable], list[dict]]:
    if mode == "capture":
        return TOOLS_MAP, TOOLS_SPEC
    return READ_ONLY_TOOLS_MAP, READ_ONLY_TOOLS_SPEC


def _with_runtime_context(messages: list[dict], backend_used: str, model_used: str | None) -> list[dict]:
    runtime_note = {
        "role": "system",
        "content": (
            "Runtime context: this response is being generated by Overseer using "
            f"backend '{backend_used}' and model '{model_used or backend_used}'. "
            "If the user asks which model or backend is active, answer with this exact runtime context."
        ),
    }
    if messages and messages[0].get("role") == "system":
        return [messages[0], runtime_note, *messages[1:]]
    return [runtime_note, *messages]


def _summarize_local_node_error(exc: Exception) -> str:
    detail = " ".join(str(exc).split()).strip()
    if detail:
        return f"local node unavailable: {detail[:160]}"
    return f"local node unavailable: {type(exc).__name__}"


# ─── backend-agnostic chat ────────────────────────────────────────────────────

async def _gemini_chat(messages: list[dict], tools_spec: list[dict]) -> dict:
    cfg = _config_copy()
    model_name = cfg["providers"]["gemini"]["model"]
    payload = {
        "model": model_name,
        "messages": _with_runtime_context(messages, "gemini", model_name),
        "tools": tools_spec,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GEMINI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GEMINI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        if not r.is_success:
            if r.status_code in (429, 503):
                raise RateLimitError(f"gemini {r.status_code}")
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:500]}")
        data = r.json()
        token_ledger[model_name] = token_ledger.get(model_name, 0) + data.get("usage", {}).get("total_tokens", 0)
        choice = data["choices"][0]["message"]
        return {
            "content": choice.get("content") or "",
            "tool_calls": choice.get("tool_calls") or [],
            "_backend_used": "gemini",
            "_model_used": model_name,
        }


async def _groq_chat(messages: list[dict], tools_spec: list[dict]) -> dict:
    # Do NOT set tool_choice — llama-3.3-70b-versatile (Hermes) generates XML
    # function call syntax when tool_choice is explicitly set, causing Groq 400.
    cfg = _config_copy()
    model_name = cfg["providers"]["groq"]["model"]
    payload = {
        "model": model_name,
        "messages": _with_runtime_context(messages, "groq", model_name),
        "tools": tools_spec,
        "parallel_tool_calls": False,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        if not r.is_success:
            if r.status_code == 429:
                raise RateLimitError("groq 429")
            err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            code = (err.get("error") or {}).get("code", "")
            # Tool use format mismatch — retry without tools so user gets a response
            if code == "tool_use_failed" or r.status_code == 400:
                r2 = await client.post(
                    f"{GROQ_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": model_name, "messages": messages},
                )
                if r2.is_success:
                    data2 = r2.json()
                    token_ledger[model_name] = token_ledger.get(model_name, 0) + data2.get("usage", {}).get("total_tokens", 0)
                    return {
                        "content": data2["choices"][0]["message"].get("content") or "",
                        "tool_calls": [],
                        "_backend_used": "groq",
                        "_model_used": model_name,
                    }
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:500]}")
        data = r.json()
        token_ledger[model_name] = token_ledger.get(model_name, 0) + data.get("usage", {}).get("total_tokens", 0)
        choice = data["choices"][0]["message"]
        return {
            "content": choice.get("content") or "",
            "tool_calls": choice.get("tool_calls") or [],
            "_backend_used": "groq",
            "_model_used": model_name,
        }


async def _openrouter_chat(messages: list[dict], model: str, tools_spec: list[dict] | None = None) -> dict:
    if not model.endswith(":free"):
        raise RuntimeError(f"OpenRouter model {model!r} is not a :free model — refusing to risk billing")
    payload = {
        "model": model,
        "messages": _with_runtime_context(messages, f"or:{model.split('/')[-1].replace(':free', '')}", model),
        "tools": tools_spec or TOOLS_SPEC,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://github.com/samhcharles/overseer",
                "X-Title": "Overseer",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if not r.is_success:
            if r.status_code == 429:
                raise RateLimitError(f"openrouter/{model} 429")
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:500]}")
        data = r.json()
        token_ledger[model] = token_ledger.get(model, 0) + data.get("usage", {}).get("total_tokens", 0)
        choice = data["choices"][0]["message"]
        slot_name = f"or:{model.split('/')[-1].replace(':free', '')}"
        return {
            "content": choice.get("content") or "",
            "tool_calls": choice.get("tool_calls") or [],
            "_backend_used": slot_name,
            "_model_used": model,
        }


async def _ollama_chat(messages: list[dict], tools_spec: list[dict]) -> dict:
    cfg = _config_copy()
    ollama_cfg = cfg["providers"]["ollama"]
    payload = {
        "model": ollama_cfg["model"],
        "messages": _with_runtime_context(messages, "ollama", ollama_cfg["model"]),
        "tools": tools_spec,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{ollama_cfg['url']}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {})
        return {
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "_backend_used": "ollama",
            "_model_used": ollama_cfg["model"],
        }


async def _remote_node_chat(messages: list[dict], tools_spec: list[dict]) -> dict:
    nodes = [node for node in _trusted_nodes() if "chat" in node.capabilities]
    if not nodes:
        raise RuntimeError("no trusted local nodes available")
    node = nodes[0]
    model_used = node.models[0] if node.models else None
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{node.inference_url.rstrip('/')}/infer/chat",
            headers={"X-Overseer-Node-Secret": OVERSEER_NODE_SECRET, "Content-Type": "application/json"},
            json={
                "messages": _with_runtime_context(messages, f"node:{node.node_id}", model_used),
                "tools": tools_spec,
            },
        )
        r.raise_for_status()
        data = r.json()
        return {
            "content": data.get("content") or "",
            "tool_calls": data.get("tool_calls") or [],
            "_backend_used": f"node:{node.node_id}",
            "_model_used": data.get("backend") or model_used,
        }


def _build_rotator() -> ProviderRotator:
    cfg = _config_copy()
    providers = cfg["providers"]
    slots: list[_Slot] = []
    # OpenRouter gets first slots (most free-model variety, 3600s TTL per model)
    if providers["openrouter"]["enabled"] and OPENROUTER_API_KEY and providers["openrouter"]["models"]:
        for or_model in providers["openrouter"]["models"]:
            short = or_model.split("/")[-1].replace(":free", "")
            slots.append(_Slot(
                name=f"or:{short}",
                call=functools.partial(_openrouter_chat, model=or_model),
                reset_ttl=3600,
            ))
    # Gemini (1M tokens/day, ~1h reset window)
    if providers["gemini"]["enabled"] and GEMINI_API_KEY:
        slots.append(_Slot(name="gemini", call=_gemini_chat, reset_ttl=3600))
    # Groq (100k tokens/day, ~8h reset window)
    if providers["groq"]["enabled"] and GROQ_API_KEY:
        slots.append(_Slot(name="groq", call=_groq_chat, reset_ttl=28800))
    # Ollama (local, retry fast — 60s in case of transient error)
    if providers["ollama"]["enabled"] and providers["ollama"]["url"]:
        slots.append(_Slot(name="ollama", call=_ollama_chat, reset_ttl=60))
    return ProviderRotator(slots)


_rotator = _build_rotator()


def _rebuild_rotator() -> None:
    global _rotator
    blocked_by_slot = {slot.name: slot.blocked_until for slot in (_rotator._slots if _rotator else [])}
    next_rotator = _build_rotator()
    for slot in next_rotator._slots:
        slot.blocked_until = blocked_by_slot.get(slot.name, 0.0)
    _rotator = next_rotator


async def llm_chat(messages: list[dict], mode: Literal["chat", "think", "capture"] = "chat") -> dict:
    fallback_reason: str | None = None
    _, tools_spec = _tool_contract_for_mode(mode)
    cfg = _config_copy()
    backend_choice = cfg["backend"]
    # Debug pin: if OVERSEER_BACKEND is set to a specific provider, skip rotation.
    if backend_choice not in ("auto", ""):
        if backend_choice == "node":
            return await _remote_node_chat(messages, tools_spec)
        if backend_choice == "ollama":
            return await _ollama_chat(messages, tools_spec)
        if backend_choice == "groq":
            return await _groq_chat(messages, tools_spec)
        if backend_choice == "openrouter":
            return await _openrouter_chat(messages, model=cfg["providers"]["openrouter"]["model"], tools_spec=tools_spec)
        if backend_choice == "gemini":
            return await _gemini_chat(messages, tools_spec)

    if cfg["prefer_local_nodes"] and OVERSEER_NODE_SECRET:
        try:
            return await _remote_node_chat(messages, tools_spec)
        except Exception as exc:
            logging.warning("overseer: local node fallback triggered: %s", exc)
            fallback_reason = _summarize_local_node_error(exc)

    # Rotation: try each slot in round-robin order, skip blocked ones.
    n = len(_rotator._slots)
    for _ in range(n + 1):
        slot = _rotator.next()
        if slot is None:
            secs = max(0, int(_rotator.soonest_reset() - time.monotonic()))
            return {
                "content": f"All providers are rate-limited. Soonest reset in ~{secs // 60}m. Try again then.",
                "tool_calls": [],
                "_backend_used": "none",
            }
        try:
            if slot.name.startswith("or:"):
                result = await slot.call(messages, tools_spec=tools_spec)
            else:
                result = await slot.call(messages, tools_spec)
            result.setdefault("_backend_used", slot.name)
            if fallback_reason:
                result.setdefault("_fallback_reason", fallback_reason)
            return result
        except RateLimitError:
            _rotator.block(slot)
        except Exception as exc:
            logging.warning("overseer: slot %s error (skipping): %s", slot.name, exc)
    return {"content": "No providers available.", "tool_calls": [], "_backend_used": "none", "_fallback_reason": fallback_reason}


async def run_tool_loop(
    messages: list[dict],
    mode: Literal["chat", "think", "capture"] = "chat",
) -> tuple[str, list[str], str, str | None, str | None]:
    tool_log: list[str] = []
    backend_used = "rotator"
    fallback_reason: str | None = None
    model_used: str | None = None
    tool_map, _ = _tool_contract_for_mode(mode)
    for _ in range(10):
        response = await llm_chat(messages, mode=mode)
        content = response["content"]
        tool_calls = response["tool_calls"]
        backend_used = response.get("_backend_used", "rotator")
        fallback_reason = response.get("_fallback_reason")
        model_used = response.get("_model_used")

        if not tool_calls:
            return content, tool_log, backend_used, fallback_reason, model_used

        # Groq format: tool_calls is list of {id, type, function: {name, arguments}}
        # Ollama format: list of {function: {name, arguments}}
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

            short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            tool_log.append(f"{name}({short_args})")

            tool_fn = tool_map.get(name)
            result = tool_fn(**args) if tool_fn else f"[tool unavailable in {mode} mode: {name}]"

            # Groq needs tool_call_id in the tool response
            tool_msg: dict = {"role": "tool", "content": str(result)[:8000]}
            if "id" in tc:
                tool_msg["tool_call_id"] = tc["id"]
            messages.append(tool_msg)

    return "Max tool iterations reached.", tool_log, backend_used, fallback_reason, model_used


# ─── log ──────────────────────────────────────────────────────────────────────

def update_log(status: str, tool_calls: list[str] | None = None, *, persist: bool = True) -> None:
    if not persist:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if _rotator:
        active, blocked = _rotator.status()
        backend_label = f"rotator ({len(active)} active, {len(blocked)} blocked)"
    else:
        backend_label = _config_copy()["backend"]
    tool_lines = "\n".join(f"- `{t}`" for t in (tool_calls or [])[-10:]) or "*no calls*"
    content = f"---\ntags: [overseer, log]\n---\n\n# Overseer Log\n\n- **{now}** — {backend_label}\n- {status}\n\n## Last calls\n\n{tool_lines}\n"
    log_path = VAULT_PATH / "memory" / "overseer-live.md"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(content)
    except Exception:
        pass


# ─── system prompt ────────────────────────────────────────────────────────────

def _build_system_prompt(mode: Literal["chat", "think", "capture"]) -> str:
    FACTS_CAP = 2000
    facts_buf = ""
    facts_dir = VAULT_PATH / "memory" / "facts"
    if facts_dir.exists():
        for f in sorted(facts_dir.glob("*.md")):
            try:
                chunk = f"\n### {f.stem}\n{f.read_text()[:600]}"
                if len(facts_buf) + len(chunk) > FACTS_CAP:
                    facts_buf += "\n[facts truncated — use vault_search for more]"
                    break
                facts_buf += chunk
            except Exception:
                pass
    facts = facts_buf or "[none yet]"

    skills_index = ""
    skills_dir = VAULT_PATH / "overseer" / "skills"
    if skills_dir.exists():
        for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            sf = d / "SKILL.md"
            if sf.exists():
                first_line = sf.read_text().splitlines()[0].lstrip("#").strip()
                skills_index += f"\n- {d.name}: {first_line}"

    mode_block = {
        "chat": """CURRENT MODE: CHAT
- Help the user understand, recall, and discuss.
- You may read, search, and fetch context.
- You may NOT write to the vault, update memory, or claim you saved anything.
- If the user asks to save something, tell them to switch to capture mode.""",
        "think": """CURRENT MODE: THINK
- This is private reasoning and planning mode.
- Help the user think, compare options, and clarify goals.
- You may read, search, and fetch context.
- You may NOT write to the vault, update memory, or claim you saved anything.""",
        "capture": """CURRENT MODE: CAPTURE
- This mode may write durable data when appropriate.
- Storing data: do it silently, confirm in one line: what was stored and where.""",
    }[mode]

    routing_block = "" if mode != "capture" else """
NO DRIFT — every write must keep the vault consistent:
- Person fact: write to memory/facts/people.md AND wiki/personal/people/NAME.md
- System change: write to memory/facts/ AND wiki/systems/NAME.md
- Before writing any wiki page: read it first and merge — never overwrite, only append or update
- Tags: lowercase kebab-case. Dates: YYYY-MM-DD always included.

ROUTING — when raw data arrives, route it:
- Person, relationship, contact: wiki/personal/people/FIRSTNAME.md
- Birthday or fact about a person: memory/facts/people.md and the person's note
- Personal preference: memory/facts/preferences.md
- Recurring schedule: memory/facts/recurring.md
- Past event: wiki/sessions/events/YYYY-MM-DD-SLUG.md
- Project info: wiki/projects/PROJECT-NAME.md
- System or infra change: wiki/systems/NAME.md
- Work session debrief: wiki/sessions/YYYY-MM-DD-SLUG.md
- Anything else raw: inbox/yap/YYYY-MM-DD-SLUG.md
- Book mentioned (titled): wiki/personal/books.md (title, author, context) — append row
- Movie mentioned (titled): wiki/personal/movies.md (title, year, context) — append row
- Article or website mentioned: wiki/personal/articles.md — append row
- New knowledge domain (not in vault after vault_search): wiki/knowledge/{{slug}}.md (create stub, tag [EMERGING])
- Anything notable and recurring that doesn't fit above (habit, language practice, workout, game, tool, goal milestone, health metric): wiki/personal/tracking/{{category-slug}}.md — create page if missing, append row
"""

    return f"""You are Overseer. You help the user work with the vault. You have tools to read, search, and sometimes write depending on mode.

RULES:
- Casual greetings, check-ins, or questions about yourself ("are you working?", "what can you do?"): respond briefly and naturally in 1-2 sentences. Do not search the vault.
- Answering factual questions about stored data: search the vault first. Return exactly what you find. If nothing found: "[not in vault: <query>]".
- Never invent or infer facts. Only state what is in the vault.
- Responses are one or two sentences unless more is explicitly requested.
- If an event is missing a date, ask exactly one question to get it.
- No filler phrases. No "I've noted that". No "Certainly!".

{mode_block}
{routing_block}

SKILLS — before performing a complex task, read the relevant skill file first:
{skills_index or "(no skills loaded)"}

Current facts:
{facts}

Today: {datetime.now(ZoneInfo(USER_TIMEZONE)).strftime("%Y-%m-%d, %A, %H:%M %Z")}"""


def system_prompt(mode: Literal["chat", "think", "capture"] = "chat") -> str:
    global _system_prompt_cache
    now = time.monotonic()
    cached = _system_prompt_cache.get(mode)
    if cached and now - cached[0] < _SYSTEM_PROMPT_TTL:
        return cached[1]
    prompt = _build_system_prompt(mode)
    _system_prompt_cache[mode] = (now, prompt)
    return prompt


# ─── entity extraction ────────────────────────────────────────────────────────

async def extract_entities(text: str) -> dict:
    words = text.split()
    if len(words) < 5 or _SKIP_EXTRACTION_RE.match(text):
        return {"entities": {}, "vault_writes": [], "skipped": True}

    tz = ZoneInfo(USER_TIMEZONE)
    local_now = datetime.now(tz)
    local_dt = local_now.strftime("%Y-%m-%dT%H:%M:%S%z")
    today = local_now.strftime("%Y-%m-%d")

    prompt = f"""Extract trackable entities from this text. User is Sam, in Seattle (America/Los_Angeles).
Current local datetime: {local_dt}

Return ONLY valid JSON, no other text:
{{
  "people": [{{"name": "string", "relation": "string", "facts": ["string"]}}],
  "events": [{{"description": "string", "date_hint": "string", "approximate": true}}],
  "todos": [{{"task": "string", "person": "string or null", "urgency": "normal"}}],
  "locations": [{{"name": "string", "context": "string"}}],
  "facts": [{{"category": "preference|recurring|personal", "content": "string"}}],
  "books": [{{"title": "string", "author": "string or null", "context": "string"}}],
  "movies": [{{"title": "string", "year": "string or null", "context": "string"}}],
  "articles": [{{"title": "string", "source": "string or null", "url": "string or null"}}],
  "knowledge_domains": [{{"domain": "string", "context": "string"}}],
  "custom_tracks": [{{"category": "string", "label": "string", "context": "string"}}]
}}

Rules:
- Only include what is explicitly stated. Do not infer.
- Interpret relative dates relative to {local_dt}.
- Mark approximate dates with "approximate": true.
- If nothing to extract for a category, use empty array.
- Extract books, movies, articles only when explicitly named — not general references.
- knowledge_domains: only when a distinct field of study or practice is clearly being engaged with.
- custom_tracks: use this for anything notable and recurring that doesn't fit above categories.
  Examples: a language being practiced, a workout routine, a game being played, a tool being learned,
  a habit, a goal milestone, a financial decision, a health metric. Be specific — category is the
  type of thing (e.g. "language_practice", "habit", "tool"), label is the specific item.
  Only include if it is clearly significant to Sam's life or work. Empty array if nothing qualifies.

Text: {text}"""

    messages = [{"role": "user", "content": prompt}]
    try:
        response = await llm_chat(messages)
        raw = response["content"]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        entities = json.loads(raw[start:end])
    except Exception as e:
        return {"error": str(e), "vault_writes": []}

    vault_writes: list[str] = []

    # People → wiki/personal/people/NAME.md + memory/facts/people.md
    for person in entities.get("people", []):
        name = (person.get("name") or "").strip()
        if not name:
            continue
        slug = name.lower().replace(" ", "-")
        person_path = f"wiki/personal/people/{slug}.md"
        existing = vault_read(person_path)
        facts_lines = "\n".join(f"- {f}" for f in person.get("facts", []))

        if "[not found:" in existing:
            relation = person.get("relation", "")
            content = (
                f"---\ntitle: {name}\npartition: personal\ntype: person\nname: {name}\n"
                f"relationship: {relation}\nbirthday: \nlast_contact: {today}\n"
                f"tags: [people, personal]\nsources: [overseer]\ncreated: {today}\nupdated: {today}\n---\n\n"
                f"# {name}\n\nPart of [[personal/MOC|Personal]].\n\n## Facts\n\n{facts_lines}\n\n## Notes\n\n## Interactions\n"
            )
        else:
            if facts_lines and "## Facts" in existing:
                content = existing.rstrip() + f"\n{facts_lines}\n"
            else:
                content = existing

        vault_write(person_path, content, f"overseer: update person {name}")
        vault_writes.append(person_path)

        if person.get("facts"):
            facts_file = "memory/facts/people.md"
            existing_facts = vault_read(facts_file)
            new_lines = "\n".join(f"- **{name}** — {f} (added {today})" for f in person["facts"])
            if "[not found:" not in existing_facts:
                updated = existing_facts.rstrip() + f"\n{new_lines}\n"
            else:
                updated = f"---\ntags: [memory, facts, people]\nupdated: {today}\n---\n\n# People Facts\n\n{new_lines}\n"
            vault_write(facts_file, updated, f"overseer: facts for {name}")
            vault_writes.append(facts_file)

    # Todos → inbox/yap/DATE-todos.md
    todos = entities.get("todos", [])
    if todos:
        todo_path = f"inbox/yap/{today}-todos.md"
        existing_todos = vault_read(todo_path)
        lines = "\n".join(
            f"- [ ] {t['task']}" + (f" (re: {t['person']})" if t.get("person") else "")
            for t in todos
        )
        if "[not found:" not in existing_todos:
            updated_todos = existing_todos.rstrip() + f"\n{lines}\n"
        else:
            updated_todos = f"---\ndate: {today}\ntags: [inbox, todos]\n---\n\n# Todos {today}\n\n{lines}\n"
        vault_write(todo_path, updated_todos, f"overseer: todos {today}")
        vault_writes.append(todo_path)

    # Events → wiki/sessions/events/DATE-slug.md
    for event in entities.get("events", []):
        desc = (event.get("description") or "").strip()
        if not desc:
            continue
        date_hint = event.get("date_hint") or today
        approx = event.get("approximate", False)
        slug_desc = desc.lower()[:30].replace(" ", "-").replace(",", "").replace(".", "")
        event_path = f"wiki/sessions/events/{today}-{slug_desc}.md"
        approx_note = " (approximate)" if approx else ""
        content = (
            f"---\ndate: {date_hint}{approx_note}\ntags: [events]\ncreated: {today}\n---\n\n"
            f"# {desc}\n\nDate: {date_hint}{approx_note}\n"
        )
        vault_write(event_path, content, f"overseer: event {slug_desc}")
        vault_writes.append(event_path)

    # Preferences + personal facts → memory/facts/preferences.md
    pref_facts = [f for f in entities.get("facts", []) if f.get("category") in ("preference", "personal")]
    if pref_facts:
        pref_path = "memory/facts/preferences.md"
        existing_prefs = vault_read(pref_path)
        lines = "\n".join(f"- {f['content']} (added {today})" for f in pref_facts)
        if "[not found:" not in existing_prefs:
            updated_prefs = existing_prefs.rstrip() + f"\n{lines}\n"
        else:
            updated_prefs = f"---\ntags: [memory, facts, preferences]\nupdated: {today}\n---\n\n# Preferences\n\n{lines}\n"
        vault_write(pref_path, updated_prefs, "overseer: update preferences")
        vault_writes.append(pref_path)

    # Recurring facts → memory/facts/recurring.md
    rec_facts = [f for f in entities.get("facts", []) if f.get("category") == "recurring"]
    if rec_facts:
        rec_path = "memory/facts/recurring.md"
        existing_rec = vault_read(rec_path)
        lines = "\n".join(f"- {f['content']} (added {today})" for f in rec_facts)
        if "[not found:" not in existing_rec:
            updated_rec = existing_rec.rstrip() + f"\n{lines}\n"
        else:
            updated_rec = f"---\ntags: [memory, facts, recurring]\nupdated: {today}\n---\n\n# Recurring\n\n{lines}\n"
        vault_write(rec_path, updated_rec, "overseer: update recurring")
        vault_writes.append(rec_path)

    # Books → wiki/personal/books.md
    for book in entities.get("books", []):
        title = (book.get("title") or "").strip()
        if not title:
            continue
        books_path = "wiki/personal/books.md"
        existing = vault_read(books_path)
        author = book.get("author") or "unknown"
        context = book.get("context") or ""
        entry = f"| {title} | {author} | {today} | {context} |"
        if "[not found:" not in existing:
            if title not in existing:
                updated = existing.rstrip() + f"\n{entry}\n"
                vault_write(books_path, updated, f"overseer: book — {title}")
                vault_writes.append(books_path)
        else:
            content = (
                f"---\ntitle: Books\npartition: personal\nsources: [overseer]\n"
                f"created: {today}\nupdated: {today}\ntags: [personal, books, media]\n---\n\n"
                f"# Books\n\nAgent-owned. Overseer appends from sessions and yap.\n\n"
                f"| Title | Author | Date mentioned | Context |\n|---|---|---|---|\n{entry}\n"
            )
            vault_write(books_path, content, f"overseer: create books.md — {title}")
            vault_writes.append(books_path)

    # Movies → wiki/personal/movies.md
    for movie in entities.get("movies", []):
        title = (movie.get("title") or "").strip()
        if not title:
            continue
        movies_path = "wiki/personal/movies.md"
        existing = vault_read(movies_path)
        year = movie.get("year") or ""
        context = movie.get("context") or ""
        entry = f"| {title} | {year} | {today} | {context} |"
        if "[not found:" not in existing:
            if title not in existing:
                updated = existing.rstrip() + f"\n{entry}\n"
                vault_write(movies_path, updated, f"overseer: movie — {title}")
                vault_writes.append(movies_path)
        else:
            content = (
                f"---\ntitle: Movies\npartition: personal\nsources: [overseer]\n"
                f"created: {today}\nupdated: {today}\ntags: [personal, movies, media]\n---\n\n"
                f"# Movies\n\nAgent-owned. Overseer appends from sessions and yap.\n\n"
                f"| Title | Year | Date mentioned | Context |\n|---|---|---|---|\n{entry}\n"
            )
            vault_write(movies_path, content, f"overseer: create movies.md — {title}")
            vault_writes.append(movies_path)

    # Articles → wiki/personal/articles.md
    for article in entities.get("articles", []):
        title = (article.get("title") or "").strip()
        if not title:
            continue
        articles_path = "wiki/personal/articles.md"
        existing = vault_read(articles_path)
        source = article.get("source") or ""
        url = article.get("url") or ""
        entry = f"| {title} | {source} | {url} | {today} |"
        if "[not found:" not in existing:
            if title not in existing:
                updated = existing.rstrip() + f"\n{entry}\n"
                vault_write(articles_path, updated, f"overseer: article — {title}")
                vault_writes.append(articles_path)
        else:
            content = (
                f"---\ntitle: Articles\npartition: personal\nsources: [overseer]\n"
                f"created: {today}\nupdated: {today}\ntags: [personal, articles, media]\n---\n\n"
                f"# Articles\n\nAgent-owned. Overseer appends from sessions and yap.\n\n"
                f"| Title | Source | URL | Date |\n|---|---|---|---|\n{entry}\n"
            )
            vault_write(articles_path, content, f"overseer: create articles.md — {title}")
            vault_writes.append(articles_path)

    # Knowledge domains → wiki/knowledge/{slug}.md
    for kd in entities.get("knowledge_domains", []):
        domain = (kd.get("domain") or "").strip()
        if not domain:
            continue
        slug = domain.lower().replace(" ", "-").replace("/", "-")
        kd_path = f"wiki/knowledge/{slug}.md"
        existing = vault_read(kd_path)
        context = kd.get("context") or ""
        if "[not found:" in existing:
            search_result = vault_search(domain)
            if "[no results]" in search_result or not search_result.strip():
                content = (
                    f"---\ntitle: {domain}\npartition: knowledge\nsources: [overseer]\n"
                    f"created: {today}\nupdated: {today}\ntags: [knowledge, emerging]\n---\n\n"
                    f"# {domain}\n\n[EMERGING] — first mentioned {today}.\n\n{context}\n\n"
                    f"Overseer will deepen this page as more sessions reference it.\n"
                )
                vault_write(kd_path, content, f"overseer: emerging domain — {domain}")
                vault_writes.append(kd_path)

    # Custom tracks → wiki/personal/tracking/{category}.md
    for ct in entities.get("custom_tracks", []):
        category = (ct.get("category") or "").strip().lower().replace(" ", "_")
        label = (ct.get("label") or "").strip()
        if not category or not label:
            continue
        slug = category.replace("_", "-")
        ct_path = f"wiki/personal/tracking/{slug}.md"
        existing = vault_read(ct_path)
        context = ct.get("context") or ""
        entry = f"| {label} | {today} | {context} |"
        if "[not found:" not in existing:
            if label not in existing:
                updated = existing.rstrip() + f"\n{entry}\n"
                vault_write(ct_path, updated, f"overseer: track {category} — {label}")
                vault_writes.append(ct_path)
        else:
            title = category.replace("_", " ").title()
            content = (
                f"---\ntitle: {title}\npartition: personal\ncategory: {category}\n"
                f"sources: [overseer]\ncreated: {today}\nupdated: {today}\n"
                f"tags: [personal, tracking, {slug}]\n---\n\n"
                f"# {title}\n\nAgent-owned. Overseer created this page from raw session data.\n\n"
                f"| Item | First seen | Context |\n|---|---|---|\n{entry}\n"
            )
            vault_write(ct_path, content, f"overseer: new track page — {category}")
            vault_writes.append(ct_path)

    return {"entities": entities, "vault_writes": vault_writes}


# ─── request models ───────────────────────────────────────────────────────────


def _model_dump(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


async def _provider_payload(cfg: dict | None = None, probes: dict | None = None) -> dict:
    config = deepcopy(cfg or _config_copy())
    probe_data = probes or await _health_probes()
    provider_statuses = probe_data.get("provider_statuses", {})
    ollama_url = config["providers"]["ollama"]["url"]
    ollama_models = await _ollama_models(ollama_url) if ollama_url else []
    trusted_nodes = [node.public_dict() for node in _trusted_nodes()]
    node_models = sorted({model for node in trusted_nodes for model in node.get("models", []) if model})

    providers = config["providers"]
    return {
        "config_path": str(RUNTIME_CONFIG_PATH),
        "backend": config["backend"],
        "available_backends": _available_backend_names(config),
        "prefer_local_nodes": config["prefer_local_nodes"],
        "providers": {
            "gemini": {
                "enabled": providers["gemini"]["enabled"],
                "configured": bool(GEMINI_API_KEY),
                "model": providers["gemini"]["model"],
                "status": provider_statuses.get("gemini", "disabled"),
            },
            "groq": {
                "enabled": providers["groq"]["enabled"],
                "configured": bool(GROQ_API_KEY),
                "model": providers["groq"]["model"],
                "status": provider_statuses.get("groq", "disabled"),
            },
            "openrouter": {
                "enabled": providers["openrouter"]["enabled"],
                "configured": bool(OPENROUTER_API_KEY),
                "model": providers["openrouter"]["model"],
                "models": providers["openrouter"]["models"],
                "status": provider_statuses.get("openrouter", "disabled"),
            },
            "ollama": {
                "enabled": providers["ollama"]["enabled"],
                "configured": bool(ollama_url),
                "url": ollama_url,
                "model": providers["ollama"]["model"],
                "available_models": ollama_models,
                "status": provider_statuses.get("ollama", "disabled"),
            },
        },
        "worker": {
            "configured": bool(WORKER_URL),
            "status": probe_data.get("worker_status", "disabled"),
        },
        "trusted_node_count": len(trusted_nodes),
        "trusted_node_models": node_models,
    }

class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    mode: Literal["chat", "think", "capture"] = "chat"


class TriageRequest(BaseModel):
    content: str
    source: str = "inbox"


class RememberRequest(BaseModel):
    fact: str
    category: str = "preferences"


class ExtractRequest(BaseModel):
    text: str
    session_id: str | None = None
    mode: Literal["chat", "think", "capture"] = "chat"


class ProviderConfigPatch(BaseModel):
    backend: str | None = None
    prefer_local_nodes: bool | None = None
    gemini_enabled: bool | None = None
    gemini_model: str | None = None
    groq_enabled: bool | None = None
    groq_model: str | None = None
    openrouter_enabled: bool | None = None
    openrouter_model: str | None = None
    openrouter_models: list[str] | None = None
    ollama_enabled: bool | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None


class ProcessRawRequest(BaseModel):
    max_sessions: int = 10


class NodeRegisterRequest(BaseModel):
    node_id: str
    hostname: str
    tailscale_ip: str
    inference_url: str
    scope: str = "owner"
    owner: str | None = None
    models: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    version: str | None = None
    secret: str


class NodeHeartbeatRequest(BaseModel):
    node_id: str
    secret: str
    models: list[str] | None = None
    capabilities: list[str] | None = None


# ─── endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
async def root(_: None = Depends(_auth)):
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/dashboard")
async def dashboard_ui(_: None = Depends(_auth)):
    return FileResponse(Path(__file__).parent / "dashboard.html")


@app.get("/status")
async def status(_: None = Depends(_auth)):
    """Aggregate founder-helper data server-side to avoid browser CORS."""
    async def fetch(path: str, timeout: float = 5.0) -> dict:
        if not FOUNDER_URL:
            return {"error": "not configured"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(f"{FOUNDER_URL}{path}")
                if r.is_success:
                    return r.json()
                return {"error": f"http {r.status_code}"}
        except Exception as e:
            return {"error": type(e).__name__, "detail": str(e)[:120]}

    # Tailscale is slow (API call); use longer timeout but cap it
    runtime, tailscale = await asyncio.gather(
        fetch("/runtime/summary", timeout=5.0),
        fetch("/tailscale/devices", timeout=8.0),
    )
    return {"runtime": runtime, "tailscale": tailscale}


@app.get("/health")
async def health():
    probes = await _health_probes()
    cfg = _config_copy()

    vault_sync = ""
    try:
        r = subprocess.run(["git", "-C", str(VAULT_PATH), "log", "-1", "--format=%ci"], capture_output=True, text=True)
        vault_sync = r.stdout.strip()
    except Exception:
        pass

    active_slots: list[str] = []
    blocked_slots: dict[str, str] = {}
    if _rotator:
        active_slots, blocked_slots = _rotator.status()
    trusted_nodes = [node.public_dict() for node in _trusted_nodes()]

    return {
        "backend": "rotator" if cfg["backend"] in ("auto", "") else cfg["backend"],
        "active_slots": active_slots,
        "blocked_slots": blocked_slots,
        "backend_status": probes["backend_status"],
        "provider_statuses": probes["provider_statuses"],
        "worker_status": probes["worker_status"],
        "vault_path": str(VAULT_PATH),
        "vault_last_sync": vault_sync,
        "token_ledger": token_ledger,
        "config_path": str(RUNTIME_CONFIG_PATH),
        "available_backends": _available_backend_names(cfg),
        "prefer_local_nodes": cfg["prefer_local_nodes"],
        "provider_config": {
            "gemini": {"enabled": cfg["providers"]["gemini"]["enabled"], "model": cfg["providers"]["gemini"]["model"]},
            "groq": {"enabled": cfg["providers"]["groq"]["enabled"], "model": cfg["providers"]["groq"]["model"]},
            "openrouter": {
                "enabled": cfg["providers"]["openrouter"]["enabled"],
                "model": cfg["providers"]["openrouter"]["model"],
                "models": cfg["providers"]["openrouter"]["models"],
            },
            "ollama": {
                "enabled": cfg["providers"]["ollama"]["enabled"],
                "url": cfg["providers"]["ollama"]["url"],
                "model": cfg["providers"]["ollama"]["model"],
            },
        },
        "node_registry_enabled": bool(OVERSEER_NODE_SECRET),
        "allowed_node_ids": sorted(OVERSEER_ALLOWED_NODE_IDS),
        "trusted_nodes": trusted_nodes,
        "trusted_node_count": len(trusted_nodes),
    }


@app.get("/ready")
async def ready():
    return {
        "status": "ok",
        "vault_path": str(VAULT_PATH),
        "node_registry_enabled": bool(OVERSEER_NODE_SECRET),
    }


@app.get("/providers")
async def get_providers(_: None = Depends(_auth)):
    probes = await _health_probes()
    return await _provider_payload(probes=probes)


@app.patch("/providers")
async def patch_providers(req: ProviderConfigPatch, _: None = Depends(_auth)):
    changes = _model_dump(req)
    if not changes:
        probes = await _health_probes()
        return await _provider_payload(probes=probes)

    patch = {"providers": {}}
    if "backend" in changes:
        backend = _clean_text(changes["backend"], "auto")
        if backend not in VALID_BACKENDS:
            raise HTTPException(status_code=400, detail=f"invalid backend: {backend}")
        patch["backend"] = backend
    if "prefer_local_nodes" in changes:
        patch["prefer_local_nodes"] = changes["prefer_local_nodes"]
    if "gemini_enabled" in changes:
        patch["providers"].setdefault("gemini", {})["enabled"] = changes["gemini_enabled"]
    if "gemini_model" in changes:
        patch["providers"].setdefault("gemini", {})["model"] = changes["gemini_model"]
    if "groq_enabled" in changes:
        patch["providers"].setdefault("groq", {})["enabled"] = changes["groq_enabled"]
    if "groq_model" in changes:
        patch["providers"].setdefault("groq", {})["model"] = changes["groq_model"]
    if "openrouter_enabled" in changes:
        patch["providers"].setdefault("openrouter", {})["enabled"] = changes["openrouter_enabled"]
    if "openrouter_model" in changes:
        model_name = _clean_text(changes["openrouter_model"])
        if model_name and not model_name.endswith(":free"):
            raise HTTPException(status_code=400, detail="openrouter_model must end with :free")
        patch["providers"].setdefault("openrouter", {})["model"] = model_name
    if "openrouter_models" in changes:
        model_list = _parse_model_list(changes["openrouter_models"], require_free=True)
        if changes["openrouter_models"] and not model_list:
            raise HTTPException(status_code=400, detail="openrouter_models must contain at least one :free model")
        patch["providers"].setdefault("openrouter", {})["models"] = model_list
    if "ollama_enabled" in changes:
        patch["providers"].setdefault("ollama", {})["enabled"] = changes["ollama_enabled"]
    if "ollama_url" in changes:
        patch["providers"].setdefault("ollama", {})["url"] = changes["ollama_url"]
    if "ollama_model" in changes:
        patch["providers"].setdefault("ollama", {})["model"] = changes["ollama_model"]

    updated_cfg = _update_runtime_config(patch)
    _rebuild_rotator()
    probes = await _health_probes()
    return await _provider_payload(cfg=updated_cfg, probes=probes)


def flush_token_ledger(*, persist: bool = True) -> None:
    if not persist:
        return
    usage_path = VAULT_PATH / "memory" / "usage.md"
    try:
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"| {model} | {tokens} | {now} |" for model, tokens in token_ledger.items()]
        header = "| Model | Total Tokens | Last Updated |\n|---|---|---|\n"
        usage_path.write_text(f"# Token Usage\n\n{header}" + "\n".join(lines) + "\n")
    except Exception:
        pass


@app.post("/chat")
async def chat(req: ChatRequest, _: None = Depends(_auth)):
    try:
        persist = req.mode == "capture"
        update_log(f"Processing: {req.message[:80]}...", persist=persist)
        messages = [
            {"role": "system", "content": system_prompt(req.mode)},
            {"role": "user", "content": req.message},
        ]
        answer, tool_log, backend_used, fallback_reason, model_used = await run_tool_loop(messages, mode=req.mode)
        flush_token_ledger(persist=persist)
        update_log(f"Done: {req.message[:60]}", tool_log, persist=persist)
        return {
            "response": answer,
            "tool_calls": tool_log,
            "backend": "rotator",
            "backend_used": backend_used,
            "model_used": model_used,
            "mode": req.mode,
            "fallback_reason": fallback_reason,
            "extracted": None,
        }
    except Exception as e:
        import traceback
        return {
            "response": None,
            "error": str(e),
            "trace": traceback.format_exc()[-1000:],
            "backend": "rotator",
            "backend_used": "none",
            "model_used": None,
            "mode": req.mode,
            "fallback_reason": None,
        }


@app.post("/extract")
async def extract(req: ExtractRequest, _: None = Depends(_auth)):
    if req.mode != "capture":
        raise HTTPException(status_code=403, detail="extract requires capture mode")
    result = await extract_entities(req.text)
    return result


@app.get("/nodes")
async def list_nodes(_: None = Depends(_auth)):
    return {
        "owner": OVERSEER_OWNER_ID or None,
        "allowed_node_ids": sorted(OVERSEER_ALLOWED_NODE_IDS),
        "nodes": [node.public_dict() for node in _trusted_nodes()],
    }


@app.post("/nodes/register")
async def register_node(req: NodeRegisterRequest, request: Request):
    client_ip = _node_access(request, req.node_id, req.secret, req.owner)
    if req.scope != "owner":
        raise HTTPException(status_code=403, detail="only owner-scoped nodes are allowed")
    _validate_node_network(req.tailscale_ip, req.inference_url)
    record = NodeRecord(
        node_id=req.node_id,
        hostname=req.hostname,
        tailscale_ip=req.tailscale_ip,
        inference_url=req.inference_url.rstrip("/"),
        scope=req.scope,
        owner=req.owner,
        models=req.models,
        capabilities=req.capabilities,
        version=req.version,
    )
    with _node_lock:
        _node_registry[req.node_id] = record
    return {"registered": True, "client_ip": client_ip, "node": record.public_dict()}


@app.post("/nodes/heartbeat")
async def heartbeat_node(req: NodeHeartbeatRequest, request: Request):
    client_ip = _node_access(request, req.node_id, req.secret)
    with _node_lock:
        record = _node_registry.get(req.node_id)
        if not record:
            raise HTTPException(status_code=404, detail="node not registered")
        record.touch(models=req.models, capabilities=req.capabilities)
        snapshot = record.public_dict()
    return {"ok": True, "client_ip": client_ip, "node": snapshot}


@app.post("/process-raw")
async def process_raw(req: ProcessRawRequest, _: None = Depends(_auth)):
    """Process unprocessed raw AI session files from vault/raw/sessions/."""
    raw_dir = VAULT_PATH / "raw" / "sessions"
    manifest_path = raw_dir / ".processed"

    if not raw_dir.exists():
        return {"processed": 0, "skipped": 0, "sessions": [], "message": "raw/sessions/ not found"}

    # Load manifest (processed session_ids)
    processed_ids: set[str] = set()
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            line = line.strip()
            if line:
                processed_ids.add(line)

    # Find unprocessed .md files (sorted oldest first by filename)
    candidates = sorted(
        [f for f in raw_dir.glob("*.md") if f.name != ".processed"],
        key=lambda f: f.name
    )

    results: list[str] = []
    skipped = 0
    processed = 0

    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    for session_file in candidates:
        if processed >= req.max_sessions:
            break

        # Parse frontmatter to check processed flag and session_id
        content = session_file.read_text()
        lines = content.splitlines()

        session_id = None
        already_processed = False

        if lines and lines[0] == "---":
            for i, line in enumerate(lines[1:], 1):
                if line == "---":
                    break
                if line.startswith("session_id:"):
                    session_id = line.split(":", 1)[1].strip()
                if line.startswith("processed:") and "true" in line.lower():
                    already_processed = True

        if already_processed or (session_id and session_id in processed_ids):
            skipped += 1
            continue

        # Extract user+assistant text turns only for the LLM prompt (not tool blobs)
        # Raw file keeps full fidelity; we send a cleaned version to the LLM
        clean_lines = []
        in_tool_block = False
        for line in lines:
            if line.startswith("### Tool:") or line.startswith("**Tool result"):
                in_tool_block = True
            elif line.startswith("### ") and in_tool_block:
                in_tool_block = False
            if not in_tool_block:
                clean_lines.append(line)

        clean_text = "\n".join(clean_lines)
        # Cap at 12000 words to stay within provider context windows
        words = clean_text.split()
        if len(words) > 12000:
            clean_text = " ".join(words[:12000]) + "\n\n[TRUNCATED — session continues in raw file]"

        session_prompt = f"""Analyze this AI session transcript and route all signal into the vault.

Use your tools (vault_read, vault_write, vault_search) to:
1. Extract and store: people, books, movies, articles, decisions, project updates, preferences, recurring events
2. Before any write: vault_read() the target page and merge — never overwrite
3. Books → wiki/personal/books.md (append row: title, author, context)
4. Movies → wiki/personal/movies.md (append row: title, year, context)
5. Project updates → find the relevant wiki/madhouse/ or wiki/orinadus/ page
6. Decisions → wiki/systems/decisions.md (append)
7. New knowledge domain (vault_search finds nothing): create wiki/knowledge/{{slug}}.md with [EMERGING] tag
8. If session contradicts existing wiki content: update the page AND append "[DRIFT RESOLVED {today}]" to wiki/_log.md
9. Only extract what is explicitly stated. No inference.

Session:
---
{clean_text[:8000]}
---

After processing, confirm what you stored and where (one line each)."""

        messages = [{"role": "system", "content": system_prompt("capture")},
                    {"role": "user", "content": session_prompt}]

        try:
            response_text, tool_log, backend, _, _ = await run_tool_loop(messages, mode="capture")
        except Exception as e:
            results.append(f"ERROR {session_file.name}: {e}")
            continue

        # Mark as processed in frontmatter
        updated_content = content.replace("processed: false", "processed: true", 1)
        session_file.write_text(updated_content)

        # Append to manifest
        if session_id:
            with manifest_path.open("a") as f:
                f.write(f"{session_id}\n")
            processed_ids.add(session_id)

        # Commit the processed flag update
        try:
            subprocess.run(["git", "-C", str(VAULT_PATH), "add", str(session_file)], check=True, capture_output=True)
            if manifest_path.exists():
                subprocess.run(["git", "-C", str(VAULT_PATH), "add", str(manifest_path)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(VAULT_PATH), "commit", "-m", f"overseer: processed session {session_file.name}"],
                check=True, capture_output=True
            )
            subprocess.run(["git", "-C", str(VAULT_PATH), "push", "origin", "main"], capture_output=True)
        except subprocess.CalledProcessError:
            pass

        results.append(session_file.name)
        processed += 1

    return {"processed": processed, "skipped": skipped, "sessions": results}


@app.post("/triage")
async def triage(req: TriageRequest, _: None = Depends(_auth)):
    triage_prompt = f"""Triage this inbox item from source: {req.source}

Return ONLY valid JSON:
{{
  "tags": ["string"],
  "destination": "wiki/orinadus | wiki/madhouse | wiki/personal | wiki/systems | wiki/sessions | wiki/personal/people | daily | ignore",
  "summary": "one sentence",
  "entities": {{
    "people": [],
    "dates": [],
    "projects": [],
    "facts": []
  }}
}}

Content:
{req.content[:3000]}"""

    messages = [{"role": "user", "content": triage_prompt}]
    response = await llm_chat(messages)
    raw = response["content"]

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end])
    except Exception:
        result = {
            "tags": ["inbox"],
            "destination": "wiki/personal",
            "summary": req.content[:100],
            "entities": {"people": [], "dates": [], "projects": [], "facts": []},
        }
    return result


@app.post("/remember")
async def remember(req: RememberRequest, _: None = Depends(_auth)):
    valid = {"people", "preferences", "recurring"}
    category = req.category if req.category in valid else "preferences"
    facts_path = f"memory/facts/{category}.md"
    existing = vault_read(facts_path)
    today = datetime.now().strftime("%Y-%m-%d")
    updated = existing.rstrip() + f"\n- **{req.fact}** (added {today})\n"
    result = vault_write(facts_path, updated, f"overseer: remember [{category}]")
    return {"stored": req.fact, "category": category, "path": facts_path, "result": result}


@app.get("/recall")
async def recall(q: str = Query(...), _: None = Depends(_auth)):
    results = vault_search(q)
    return {"query": q, "results": results}


@app.get("/logs")
async def logs(_: None = Depends(_auth)):
    async def gen():
        log_path = VAULT_PATH / "memory" / "overseer-live.md"
        last_mtime = 0.0
        for _ in range(120):
            try:
                mtime = log_path.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    content = log_path.read_text()
                    yield f"data: {json.dumps({'content': content})}\n\n"
            except Exception:
                pass
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream")
