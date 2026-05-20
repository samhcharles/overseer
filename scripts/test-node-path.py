#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {url}: {last_error}")


def request_json(url: str, *, data: dict | None = None, headers: dict[str, str] | None = None, method: str | None = None) -> dict:
    encoded = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=encoded, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode())


def main() -> int:
    python = sys.executable
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mock = tmp / "mock_node.py"
        mock.write_text(
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "import json\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_POST(self):\n"
            "        if self.path != '/infer/chat':\n"
            "            self.send_response(404); self.end_headers(); return\n"
            "        length = int(self.headers.get('Content-Length', '0'))\n"
            "        self.rfile.read(length)\n"
            "        body = json.dumps({'content': 'node ok', 'tool_calls': []}).encode()\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, fmt, *args):\n"
            "        pass\n"
            "HTTPServer(('127.0.0.1', 18765), Handler).serve_forever()\n"
        )

        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "OVERSEER_NODE_SECRET": "test-secret",
                "OVERSEER_ALLOWED_NODE_IDS": "node-1",
                "OVERSEER_OWNER_ID": "sam",
                "OVERSEER_API_KEY": "test-key",
                "OLLAMA_URL": "",
            }
        )

        node = subprocess.Popen([python, str(mock)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gateway = subprocess.Popen(
            [python, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8766"],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_http("http://127.0.0.1:8766/health")
            register = request_json(
                "http://127.0.0.1:8766/nodes/register",
                data={
                    "node_id": "node-1",
                    "hostname": "sam-laptop",
                    "tailscale_ip": "100.101.102.103",
                    "inference_url": "http://127.0.0.1:18765",
                    "scope": "owner",
                    "owner": "sam",
                    "models": ["mock-model"],
                    "capabilities": ["chat"],
                    "version": "test",
                    "secret": "test-secret",
                },
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            nodes = request_json(
                "http://127.0.0.1:8766/nodes",
                headers={"Authorization": "Bearer test-key"},
            )
            health = request_json("http://127.0.0.1:8766/health")
            chat = request_json(
                "http://127.0.0.1:8766/chat",
                data={"message": "Say hello without tools."},
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )

            assert register["registered"] is True, register
            assert nodes["nodes"][0]["node_id"] == "node-1", nodes
            assert health["trusted_node_count"] == 1, health
            assert chat["backend_used"] == "node:node-1", chat
            print("node-path smoke ok")
            return 0
        finally:
            gateway.terminate()
            node.terminate()
            for proc in (gateway, node):
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
