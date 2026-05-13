# Local Dashboard

MemPalace includes a read-only local web dashboard for inspecting memories.
It runs on your machine, binds to `127.0.0.1` by default, and does not add,
edit, or delete drawers.

## Start

```bash
mempalace dashboard
```

By default this opens:

```text
http://127.0.0.1:8765/
```

Use another port if needed:

```bash
mempalace dashboard --port 8877
```

Run without opening a browser:

```bash
mempalace dashboard --no-open
```

Use a custom palace:

```bash
mempalace --palace /path/to/palace dashboard
```

## Features

- Palace overview: total drawers, wings, rooms, authors, and sources.
- Drawer browser with filters for wing, room, author, and literal text.
- Semantic search using the configured embedding model.
- Detail panel with full drawer content and metadata.

## Safety

The dashboard is read-only. It uses the same collection access path as the CLI
and MCP server, so it honors the active MemPalace config, including custom
embedding model settings such as `Qwen/Qwen3-Embedding-0.6B`.

The server is intended for local use. Keep the default `--host 127.0.0.1`
unless you have added your own network access controls.
