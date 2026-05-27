# @samhcus/overseer

One-command install for the Overseer TUI — the Bubble Tea terminal client for [Overseer](https://github.com/samhcus/overseer), a local-first AI backed by your Obsidian vault.

## Install

```bash
npm install -g @samhcus/overseer
overseer
```

The postinstall step downloads the right platform binary from the matching GitHub Release. Supported platforms:

- `linux-amd64`, `linux-arm64`
- `darwin-amd64`, `darwin-arm64`
- `windows-amd64`

## Update

```bash
npm update -g @samhcus/overseer
```

## Backend

The TUI talks to an Overseer API node (FastAPI). See the [main repo README](https://github.com/samhcus/overseer) for setting up the API and vault.

## License

MIT
