# overseer

terminal AI that talks to your vault.

```
ollama (local model)
    │
api/        FastAPI node, reads and writes ~/vault
    │
tui-go/     Bubble Tea TUI, single binary
```

## run

**API node:**

```bash
cd api
pip install -r requirements.txt
./start.sh
```

Reads `~/.secrets/overseer.env` if present. Needs Ollama running.

**TUI:**

```bash
cd tui-go
./build.sh --install   # builds and puts `overseer` in ~/.local/bin
overseer
```

## config

| var | default | purpose |
|---|---|---|
| `OVERSEER_API_URL` | `http://localhost:7860` | API node |
| `VAULT_PATH` | `~/vault` | vault root |
| `OLLAMA_MODEL` | `dolphin3:latest` | model |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama |

## keys

| key | action |
|---|---|
| `enter` | send |
| `ctrl+j` | newline |
| `tab` | chat / vault |
| `esc` | cancel |
| `↑ ↓` | scroll / navigate |
| `pgup pgdn` | scroll messages |
| `-` | vault: go up a directory |
| `ctrl+c` | quit |
