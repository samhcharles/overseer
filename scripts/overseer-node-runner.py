#!/usr/bin/env python3
"""
Secure Overseer node runner.

- Registers a local model node with the gateway
- Exposes a small authenticated inference bridge for the gateway
- Intended for a single user's own Tailscale or private-network devices
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket as _socket
import subprocess
from contextlib import suppress

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn


GATEWAY_URL = os.environ.get("OVERSEER_GATEWAY_URL", os.environ.get("OVERSEER_API_URL", "")).rstrip("/")
NODE_SECRET = os.environ.get("OVERSEER_NODE_SECRET", "")
NODE_ID = os.environ.get("OVERSEER_NODE_ID") or os.environ.get("HOSTNAME", "overseer-node")
NODE_HOSTNAME = os.environ.get("OVERSEER_NODE_HOSTNAME") or os.environ.get("HOSTNAME", NODE_ID)
NODE_OWNER = os.environ.get("OVERSEER_OWNER_ID", "").strip() or None
NODE_PORT = int(os.environ.get("OVERSEER_NODE_PORT", "18765"))
NODE_BIND = os.environ.get("OVERSEER_NODE_BIND", "0.0.0.0")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
HEARTBEAT_INTERVAL = int(os.environ.get("OVERSEER_NODE_HEARTBEAT_INTERVAL", "30"))

app = FastAPI(title="Overseer Node Runner", version="1.0.0")
logging.basicConfig(level=logging.INFO)


def _sd_notify(state: str) -> None:
    """Send a notification to systemd via NOTIFY_SOCKET (sd_notify protocol)."""
    sock_path = os.environ.get("NOTIFY_SOCKET", "")
    if not sock_path:
        return
    with suppress(Exception):
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM) as sock:
            addr = "\0" + sock_path[1:] if sock_path.startswith("@") else sock_path
            sock.connect(addr)
            sock.sendall(state.encode())


class InferChatRequest(BaseModel):
    messages: list[dict]
    tools: list[dict] | None = None


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


def _require_gateway(request: Request) -> None:
    if not NODE_SECRET:
        raise HTTPException(status_code=503, detail="node secret not configured")
    if request.headers.get("X-Overseer-Node-Secret", "") != NODE_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")
    client_ip = _request_ip(request)
    if not _is_private_or_tailscale(client_ip):
        raise HTTPException(status_code=403, detail=f"private or tailscale access required, got {client_ip}")


def _tailscale_ip() -> str:
    env_ip = os.environ.get("OVERSEER_NODE_TAILSCALE_IP", "").strip()
    if env_ip:
        return env_ip
    with suppress(Exception):
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True)
        ips = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if ips:
            return ips[0]
    return "127.0.0.1"


async def _ollama_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [item.get("name") for item in data.get("models", []) if item.get("name")]
    except Exception:
        return [OLLAMA_MODEL] if OLLAMA_MODEL else []


async def _register() -> None:
    if not GATEWAY_URL or not NODE_SECRET:
        return
    tailscale_ip = _tailscale_ip()
    payload = {
        "node_id": NODE_ID,
        "hostname": NODE_HOSTNAME,
        "tailscale_ip": tailscale_ip,
        "inference_url": f"http://{tailscale_ip}:{NODE_PORT}",
        "scope": "owner",
        "owner": NODE_OWNER,
        "models": await _ollama_models(),
        "capabilities": ["chat"],
        "version": "1.0.0",
        "secret": NODE_SECRET,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{GATEWAY_URL}/nodes/register", json=payload)
        response.raise_for_status()


async def _heartbeat_loop() -> None:
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{GATEWAY_URL}/nodes/heartbeat",
                    json={
                        "node_id": NODE_ID,
                        "secret": NODE_SECRET,
                        "models": await _ollama_models(),
                        "capabilities": ["chat"],
                    },
                )
                response.raise_for_status()
            _sd_notify("WATCHDOG=1")
        except Exception as exc:
            logging.warning("overseer-node: heartbeat failed: %s", exc)
            with suppress(Exception):
                await _register()
        await asyncio.sleep(HEARTBEAT_INTERVAL)


@app.on_event("startup")
async def startup() -> None:
    await _register()
    _sd_notify("READY=1")
    if GATEWAY_URL and NODE_SECRET:
        asyncio.create_task(_heartbeat_loop())


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "node_id": NODE_ID,
        "hostname": NODE_HOSTNAME,
        "tailscale_ip": _tailscale_ip(),
        "gateway": GATEWAY_URL or None,
    }


@app.post("/infer/chat")
async def infer_chat(req: InferChatRequest, request: Request) -> dict:
    _require_gateway(request)
    model = OLLAMA_MODEL
    if not model:
        models = await _ollama_models()
        if not models:
            raise HTTPException(status_code=503, detail="no local models available")
        model = models[0]
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": req.messages,
                "tools": req.tools or [],
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        return {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "backend": model,
        }


if __name__ == "__main__":
    uvicorn.run(app, host=NODE_BIND, port=NODE_PORT)
