#!/usr/bin/env bash
# Overseer first-run wizard.
# Scaffolds a fresh Obsidian vault + writes .env + verifies prerequisites.
# Idempotent: safe to re-run; existing files are not overwritten.

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
TEMPLATES="$REPO_ROOT/templates"

echo "═══════════════════════════════════════════════════════"
echo "  Overseer — first-run setup"
echo "═══════════════════════════════════════════════════════"
echo

# ─── Vault path ──────────────────────────────────────────────────────────────
read -rp "Vault path [$HOME/vault]: " VAULT_PATH_INPUT
VAULT_PATH="${VAULT_PATH_INPUT:-$HOME/vault}"

if [ -d "$VAULT_PATH" ] && [ -n "$(ls -A "$VAULT_PATH" 2>/dev/null)" ]; then
  echo "⚠  $VAULT_PATH already exists and is non-empty."
  read -rp "   Use it anyway? Existing files will be kept. (y/N): " keep
  [ "$keep" != "y" ] && { echo "Aborting."; exit 1; }
fi

mkdir -p "$VAULT_PATH"
echo "✓ vault root: $VAULT_PATH"

# ─── Partition scaffolding ───────────────────────────────────────────────────
PARTITIONS=(
  orinadus madhouse personal finance health calendar places knowledge systems
  sales bookmarks reading journal projects contacts travel inbox-ideas inbox-novel
)
echo "Scaffolding ${#PARTITIONS[@]} partitions under $VAULT_PATH/wiki/ ..."
for p in "${PARTITIONS[@]}"; do
  mkdir -p "$VAULT_PATH/wiki/$p"
  if [ ! -f "$VAULT_PATH/wiki/$p/index.md" ]; then
    src="$TEMPLATES/wiki/$p/index.md"
    if [ -f "$src" ]; then
      cp "$src" "$VAULT_PATH/wiki/$p/index.md"
    fi
  fi
done

# Subfolders for known partitions
mkdir -p "$VAULT_PATH/wiki/sales/"{deals,leads,clients}
mkdir -p "$VAULT_PATH/wiki/reading/"{books,articles,courses,podcasts}
mkdir -p "$VAULT_PATH/wiki/contacts/"{business,vendors,suppliers}
mkdir -p "$VAULT_PATH/wiki/travel/trips"
mkdir -p "$VAULT_PATH/wiki/personal/people"
mkdir -p "$VAULT_PATH/wiki/finance/"{transactions,budgets,accounts}
mkdir -p "$VAULT_PATH/wiki/health/daily"
mkdir -p "$VAULT_PATH/wiki/calendar/"{events,recurring}
mkdir -p "$VAULT_PATH/wiki/places/visits"

# Top-level non-wiki dirs
mkdir -p "$VAULT_PATH/"{raw,inbox/sleipnir,inbox/sessions,inbox/yap,memory/facts,outputs,dashboards,templates,daily}

# ─── VAULT.md schema doc ─────────────────────────────────────────────────────
if [ ! -f "$VAULT_PATH/VAULT.md" ]; then
  if [ -f "$TEMPLATES/VAULT.md" ]; then
    cp "$TEMPLATES/VAULT.md" "$VAULT_PATH/VAULT.md"
    echo "✓ VAULT.md schema doc copied"
  else
    echo "⚠ $TEMPLATES/VAULT.md not found — vault schema doc not created"
  fi
fi

# ─── Dashboards ──────────────────────────────────────────────────────────────
if [ -d "$TEMPLATES/dashboards" ]; then
  for d in "$TEMPLATES/dashboards"/*.md; do
    [ -e "$d" ] || continue
    dest="$VAULT_PATH/dashboards/$(basename "$d")"
    if [ ! -f "$dest" ]; then
      cp "$d" "$dest"
    fi
  done
  echo "✓ dashboards scaffolded"
fi

# ─── Obsidian config ─────────────────────────────────────────────────────────
if [ -d "$TEMPLATES/.obsidian" ] && [ ! -d "$VAULT_PATH/.obsidian" ]; then
  cp -r "$TEMPLATES/.obsidian" "$VAULT_PATH/.obsidian"
  echo "✓ Obsidian config scaffolded (community plugins listed; install via Obsidian UI on first open)"
fi

# ─── Git init ────────────────────────────────────────────────────────────────
if [ ! -d "$VAULT_PATH/.git" ]; then
  git -C "$VAULT_PATH" init -q
  git -C "$VAULT_PATH" config user.email "overseer@local"
  git -C "$VAULT_PATH" config user.name "Overseer"
  echo "✓ vault initialized as git repo"
fi

# ─── Provider choice ─────────────────────────────────────────────────────────
echo
echo "Default LLM provider:"
echo "  1) Ollama (local, default — needs ollama installed)"
echo "  2) Gemini (needs GEMINI_API_KEY)"
echo "  3) OpenRouter (needs OPENROUTER_API_KEY)"
read -rp "Choice [1]: " PROVIDER_CHOICE
PROVIDER_CHOICE="${PROVIDER_CHOICE:-1}"
case "$PROVIDER_CHOICE" in
  1) BACKEND=ollama ;;
  2) BACKEND=gemini ;;
  3) BACKEND=openrouter ;;
  *) BACKEND=auto ;;
esac

# ─── .env ────────────────────────────────────────────────────────────────────
if [ ! -f "$REPO_ROOT/.env" ]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  # patch VAULT_PATH and OVERSEER_BACKEND
  sed -i "s|^VAULT_PATH=.*|VAULT_PATH=$VAULT_PATH|" "$REPO_ROOT/.env"
  sed -i "s|^OVERSEER_BACKEND=.*|OVERSEER_BACKEND=$BACKEND|" "$REPO_ROOT/.env"
  echo "✓ .env written (provider: $BACKEND, vault: $VAULT_PATH)"
else
  echo "ℹ .env already exists — not overwritten"
fi

# ─── Ollama health check ─────────────────────────────────────────────────────
if [ "$BACKEND" = "ollama" ]; then
  echo
  echo "Checking Ollama..."
  if curl -fs http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "✓ Ollama reachable at http://localhost:11434"
  else
    echo "⚠ Ollama is NOT reachable at http://localhost:11434"
    echo "  Install:   curl -fsSL https://ollama.com/install.sh | sh"
    echo "  Start:     ollama serve   (in another terminal)"
    echo "  Pull a model: ollama pull dolphin3"
  fi
fi

echo
echo "═══════════════════════════════════════════════════════"
echo "  Setup complete."
echo
echo "  Next:"
echo "    cd api && pip install -r requirements.txt && ./start.sh"
echo "    cd tui-go && ./build.sh --install && overseer"
echo "═══════════════════════════════════════════════════════"
