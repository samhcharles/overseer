# @samhcharles/overseer

One-command install for the Overseer TUI — the Bubble Tea terminal client for [Overseer](https://github.com/samhcharles/overseer), a local-first AI backed by your Obsidian vault.

## Install

```bash
npm install -g @samhcharles/overseer
overseer
```

The postinstall step downloads the right platform binary from the matching GitHub Release. Supported platforms:

- `linux-amd64`, `linux-arm64`
- `darwin-amd64`, `darwin-arm64`
- `windows-amd64`

## Update

```bash
npm update -g @samhcharles/overseer
```

## Backend

The TUI talks to an Overseer API node (FastAPI). See the [main repo README](https://github.com/samhcharles/overseer) for setting up the API and vault.

## License

MIT
