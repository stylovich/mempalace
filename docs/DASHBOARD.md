# Local Dashboard

MemPalace includes a local web dashboard for inspecting memories. It runs on
your machine and binds to `127.0.0.1` by default.

The dashboard is read-only unless you explicitly start it with `--write`.

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

Enable editing and deletion:

```bash
mempalace dashboard --write
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
- Optional `--write` mode for editing drawer metadata/content and deleting
  drawers.

## Safety

The dashboard is read-only by default. Edit and delete endpoints return `403`
unless the server was started with `--write`.

In write mode, editing a drawer rewrites the document through ChromaDB so the
embedding is regenerated with the active MemPalace config, including custom
embedding model settings such as `Qwen/Qwen3-Embedding-0.6B`.

The server is intended for local use. Keep the default `--host 127.0.0.1`
unless you have added your own network access controls.
