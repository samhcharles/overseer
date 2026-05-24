#!/usr/bin/env bash
# Build and install the overseer TUI.
# Usage: ./build.sh [--install]
set -e
cd "$(dirname "$0")"

GOTOOLCHAIN=auto go build -o overseer .

if [[ "$1" == "--install" ]]; then
  cp overseer ~/.local/bin/overseer
  echo "installed to ~/.local/bin/overseer"
else
  echo "built: $(pwd)/overseer"
fi
