"""Local read-only web dashboard for browsing a MemPalace palace."""

from __future__ import annotations

import json
import os
import posixpath
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import MempalaceConfig
from .palace import get_collection
from .searcher import search_memories


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_LIMIT = 200


def _as_int(raw: str | None, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _first(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    value = values[0].strip()
    return value if value else default


def _where_filter(
    *,
    wing: str | None = None,
    room: str | None = None,
    source_file: str | None = None,
    added_by: str | None = None,
) -> dict:
    clauses = []
    if wing:
        clauses.append({"wing": wing})
    if room:
        clauses.append({"room": room})
    if source_file:
        clauses.append({"source_file": source_file})
    if added_by:
        clauses.append({"added_by": added_by})
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _preview(text: str, max_chars: int = 260) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _public_metadata(metadata: dict | None) -> dict:
    meta = dict(metadata or {})
    source = meta.get("source_file")
    if source:
        meta["source_file_name"] = Path(str(source)).name
    return meta


def _drawer_item(drawer_id: str, document: str, metadata: dict | None) -> dict:
    meta = _public_metadata(metadata)
    return {
        "drawer_id": drawer_id,
        "wing": meta.get("wing", "unknown"),
        "room": meta.get("room", "unknown"),
        "source_file": meta.get("source_file"),
        "source_file_name": meta.get("source_file_name"),
        "added_by": meta.get("added_by"),
        "created_at": meta.get("filed_at"),
        "chunk_index": meta.get("chunk_index"),
        "content_preview": _preview(document),
        "content": document,
        "metadata": meta,
    }


class DashboardApp:
    """Small app object used by the HTTP handler and unit tests."""

    def __init__(self, palace_path: str, collection_name: str | None = None):
        self.palace_path = os.path.abspath(os.path.expanduser(palace_path))
        self.collection_name = collection_name

    def _collection(self):
        return get_collection(
            self.palace_path,
            collection_name=self.collection_name,
            create=False,
        )

    def status(self) -> dict:
        col = self._collection()
        total = col.count()
        rows = col.get(include=["metadatas"])
        wings: dict[str, int] = {}
        rooms: dict[str, int] = {}
        rooms_by_wing: dict[str, dict[str, int]] = {}
        added_by: dict[str, int] = {}
        sources: dict[str, int] = {}

        for meta in rows.metadatas:
            meta = meta or {}
            wing = str(meta.get("wing") or "unknown")
            room = str(meta.get("room") or "unknown")
            wings[wing] = wings.get(wing, 0) + 1
            rooms[room] = rooms.get(room, 0) + 1
            rooms_by_wing.setdefault(wing, {})
            rooms_by_wing[wing][room] = rooms_by_wing[wing].get(room, 0) + 1
            author = meta.get("added_by")
            if author:
                author = str(author)
                added_by[author] = added_by.get(author, 0) + 1
            source = meta.get("source_file")
            if source:
                source = str(source)
                sources[source] = sources.get(source, 0) + 1

        return {
            "palace_path": self.palace_path,
            "collection_name": self.collection_name or MempalaceConfig().collection_name,
            "total_drawers": total,
            "wings": dict(sorted(wings.items())),
            "rooms": dict(sorted(rooms.items())),
            "rooms_by_wing": {
                wing: dict(sorted(room_counts.items()))
                for wing, room_counts in sorted(rooms_by_wing.items())
            },
            "added_by": dict(sorted(added_by.items())),
            "sources": dict(sorted(sources.items())),
        }

    def list_drawers(self, params: dict[str, list[str]]) -> dict:
        limit = _as_int(_first(params, "limit"), 50, minimum=1, maximum=MAX_LIMIT)
        offset = _as_int(_first(params, "offset"), 0, minimum=0)
        where = _where_filter(
            wing=_first(params, "wing"),
            room=_first(params, "room"),
            source_file=_first(params, "source_file"),
            added_by=_first(params, "added_by"),
        )
        contains = _first(params, "contains")
        where_document = {"$contains": contains} if contains else None

        col = self._collection()
        total = col.count()
        kwargs: dict[str, Any] = {
            "include": ["documents", "metadatas"],
            "limit": limit,
            "offset": offset,
        }
        if where:
            kwargs["where"] = where
        if where_document:
            kwargs["where_document"] = where_document
        rows = col.get(**kwargs)
        drawers = [
            _drawer_item(drawer_id, doc or "", meta or {})
            for drawer_id, doc, meta in zip(rows.ids, rows.documents, rows.metadatas)
        ]
        return {
            "drawers": drawers,
            "count": len(drawers),
            "total": total,
            "offset": offset,
            "limit": limit,
            "filters": {
                "wing": _first(params, "wing"),
                "room": _first(params, "room"),
                "source_file": _first(params, "source_file"),
                "added_by": _first(params, "added_by"),
                "contains": contains,
            },
        }

    def get_drawer(self, drawer_id: str) -> dict:
        rows = self._collection().get(
            ids=[drawer_id],
            include=["documents", "metadatas"],
        )
        if not rows.ids:
            raise KeyError(drawer_id)
        return _drawer_item(
            rows.ids[0],
            rows.documents[0] if rows.documents else "",
            rows.metadatas[0],
        )

    def search(self, payload: dict) -> dict:
        query = str(payload.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        limit = _as_int(str(payload.get("limit") or ""), 10, minimum=1, maximum=MAX_LIMIT)
        max_distance = payload.get("max_distance", 0.0)
        try:
            max_distance = float(max_distance or 0.0)
        except (TypeError, ValueError):
            max_distance = 0.0
        return search_memories(
            query=query,
            palace_path=self.palace_path,
            wing=payload.get("wing") or None,
            room=payload.get("room") or None,
            n_results=limit,
            max_distance=max_distance,
            collection_name=self.collection_name,
        )


class _DashboardHandler(BaseHTTPRequestHandler):
    server_version = "MemPalaceDashboard/0.1"

    @property
    def app(self) -> DashboardApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        raw_len = self.headers.get("Content-Length", "0")
        length = _as_int(raw_len, 0, minimum=0, maximum=1_000_000)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = posixpath.normpath(parsed.path)
        params = parse_qs(parsed.query)
        try:
            if path in {"/", "/dashboard"}:
                self._send_html()
            elif path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/api/status":
                self._send_json(self.app.status())
            elif path == "/api/drawers":
                self._send_json(self.app.list_drawers(params))
            elif path.startswith("/api/drawers/"):
                drawer_id = path.rsplit("/", 1)[-1]
                self._send_json(self.app.get_drawer(drawer_id))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except KeyError:
            self._send_json({"error": "drawer not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001 - dashboard must report backend errors as JSON
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = posixpath.normpath(parsed.path)
        try:
            if path == "/api/search":
                self._send_json(self.app.search(self._read_json()))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, app: DashboardApp):
        super().__init__(server_address, _DashboardHandler)
        self.app = app


def serve_dashboard(
    *,
    palace_path: str | None = None,
    collection_name: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    cfg = MempalaceConfig()
    resolved_palace = palace_path or cfg.palace_path
    app = DashboardApp(resolved_palace, collection_name=collection_name)
    server = DashboardServer((host, port), app)
    url = f"http://{host}:{server.server_port}/"
    print(f"MemPalace dashboard: {url}")
    print(f"Palace: {app.palace_path}")
    print("Read-only mode. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MemPalace Dashboard</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-2: #f0f3f6;
      --text: #17202a;
      --muted: #657382;
      --line: #d8dee5;
      --accent: #126b65;
      --accent-2: #0f8b7d;
      --danger: #b3261e;
      --shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #101417;
        --panel: #181d21;
        --panel-2: #20272d;
        --text: #edf2f5;
        --muted: #a8b3bd;
        --line: #313a42;
        --accent: #4db6a9;
        --accent-2: #6fcabd;
        --danger: #ffb4ab;
        --shadow: none;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    button, input, select, textarea {
      font: inherit;
      color: inherit;
    }
    .shell {
      display: grid;
      grid-template-columns: minmax(220px, 280px) minmax(360px, 1fr) minmax(360px, 46vw);
      min-height: 100vh;
    }
    aside, main, .detail {
      min-width: 0;
      border-right: 1px solid var(--line);
    }
    aside, .detail {
      background: var(--panel);
    }
    header {
      padding: 16px;
      border-bottom: 1px solid var(--line);
    }
    h1, h2 {
      margin: 0;
      font-size: 16px;
      line-height: 1.25;
      letter-spacing: 0;
    }
    h2 { font-size: 14px; }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
    }
    .stat {
      padding: 10px;
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 6px;
    }
    .stat strong { display: block; font-size: 18px; }
    .filters {
      padding: 12px 16px;
      display: grid;
      gap: 10px;
      border-bottom: 1px solid var(--line);
    }
    label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    select, input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      padding: 8px 9px;
      outline: none;
    }
    select:focus, input:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 25%, transparent);
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    button {
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      white-space: nowrap;
    }
    button.primary {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    button:hover { border-color: var(--accent-2); }
    .list {
      display: grid;
      gap: 8px;
      padding: 12px;
    }
    .item {
      text-align: left;
      width: 100%;
      min-width: 0;
      overflow: hidden;
      white-space: normal;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px;
      box-shadow: var(--shadow);
      display: grid;
      gap: 6px;
    }
    .item.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 20%, transparent);
    }
    .path {
      display: flex;
      gap: 6px;
      align-items: center;
      min-width: 0;
      color: var(--accent);
      font-weight: 650;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      max-width: 100%;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 12px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .meta-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .preview {
      color: var(--text);
      overflow-wrap: anywhere;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .detail {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      max-height: 100vh;
      position: sticky;
      top: 0;
      border-right: 0;
    }
    .detail-meta {
      padding: 12px 16px;
      display: grid;
      gap: 8px;
      border-bottom: 1px solid var(--line);
    }
    .content {
      padding: 16px;
      overflow: auto;
      font-size: 14px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .content > :first-child { margin-top: 0; }
    .content > :last-child { margin-bottom: 0; }
    .content h1, .content h2, .content h3 {
      margin: 16px 0 8px;
      line-height: 1.25;
    }
    .content h1 { font-size: 20px; }
    .content h2 { font-size: 17px; }
    .content h3 { font-size: 15px; }
    .content p { margin: 0 0 10px; }
    .content ul, .content ol { margin: 0 0 12px 22px; padding: 0; }
    .content blockquote {
      margin: 0 0 12px;
      padding-left: 12px;
      border-left: 3px solid var(--line);
      color: var(--muted);
    }
    .content pre {
      margin: 0 0 12px;
      padding: 12px;
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: auto;
      white-space: pre-wrap;
    }
    .content code {
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 1px 4px;
    }
    .content pre code {
      background: transparent;
      border: 0;
      padding: 0;
    }
    .content a { color: var(--accent-2); }
    .content hr {
      border: 0;
      border-top: 1px solid var(--line);
      margin: 16px 0;
    }
    .content table {
      width: 100%;
      margin: 0 0 12px;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .content th, .content td {
      border: 1px solid var(--line);
      padding: 6px 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    .content th {
      background: var(--panel-2);
      color: var(--muted);
      font-weight: 650;
    }
    .structured-memory {
      display: grid;
      gap: 2px;
    }
    .structured-row {
      display: grid;
      grid-template-columns: minmax(92px, 180px) minmax(0, 1fr);
      gap: 12px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }
    .structured-row:first-child { padding-top: 0; }
    .structured-row:last-child {
      padding-bottom: 0;
      border-bottom: 0;
    }
    .structured-key {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .structured-value { min-width: 0; }
    .structured-stars {
      color: var(--accent);
      letter-spacing: 1px;
    }
    .empty, .error {
      margin: 16px;
      padding: 14px;
      border: 1px dashed var(--line);
      border-radius: 7px;
      color: var(--muted);
    }
    .error {
      color: var(--danger);
      border-color: color-mix(in srgb, var(--danger) 50%, var(--line));
    }
    @media (max-width: 980px) {
      .shell {
        grid-template-columns: 1fr;
      }
      aside, main, .detail {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .detail {
        position: static;
        max-height: none;
      }
      .structured-row {
        grid-template-columns: 1fr;
        gap: 2px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <header>
        <h1>MemPalace</h1>
        <div class="small muted" id="palacePath">Loading…</div>
      </header>
      <section class="stats">
        <div class="stat"><strong id="drawerCount">0</strong><span class="small muted">drawers</span></div>
        <div class="stat"><strong id="wingCount">0</strong><span class="small muted">wings</span></div>
      </section>
      <section class="filters">
        <label>Wing<select id="wingFilter"><option value="">All wings</option></select></label>
        <label>Room<select id="roomFilter"><option value="">All rooms</option></select></label>
        <label>Added by<select id="addedByFilter"><option value="">Anyone</option></select></label>
        <label>Text contains<input id="containsFilter" placeholder="literal substring" /></label>
        <button id="clearFilters">Clear filters</button>
      </section>
    </aside>
    <main>
      <div class="toolbar">
        <input id="searchInput" placeholder="Semantic search…" />
        <button id="searchButton" class="primary">Search</button>
        <button id="listButton">List</button>
      </div>
      <div class="list" id="results"></div>
    </main>
    <section class="detail">
      <header>
        <h2 id="detailTitle">Select a drawer</h2>
        <div class="small muted" id="detailSubtitle">Read-only dashboard</div>
      </header>
      <div class="detail-meta" id="detailMeta"></div>
      <div class="content" id="detailContent"></div>
    </section>
  </div>
  <script>
    const state = { status: null, selected: null, mode: "list" };
    const $ = (id) => document.getElementById(id);

    function escapeHtml(text) {
      return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    function formatDate(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short"
      }).format(date);
    }

    function renderInlineMarkdown(text) {
      let html = escapeHtml(text);
      html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      html = html.replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer">$1</a>'
      );
      return html;
    }

    function splitTableRow(line) {
      let normalized = line.trim();
      if (normalized.startsWith("|")) normalized = normalized.slice(1);
      if (normalized.endsWith("|")) normalized = normalized.slice(0, -1);
      return normalized.split("|").map((cell) => cell.trim());
    }

    function isMarkdownTableSeparator(line) {
      const cells = splitTableRow(line);
      return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
    }

    function renderTable(headers, rows) {
      const head = headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
      const body = rows.map((row) => {
        const cells = headers.map((_, index) => `<td>${renderInlineMarkdown(row[index] || "")}</td>`).join("");
        return `<tr>${cells}</tr>`;
      }).join("");
      return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    function looksLikePipeMemory(text) {
      const raw = String(text || "").trim();
      if (!raw || raw.includes("\n\n")) return false;
      const parts = raw.split("|").map((part) => part.trim()).filter(Boolean);
      if (parts.length < 4) return false;
      const keyed = parts.filter((part) => /^[A-Za-z0-9_.-]{2,48}[:=]/.test(part));
      return keyed.length >= Math.ceil(parts.length / 2);
    }

    function splitStructuredPart(part) {
      const colon = part.indexOf(":");
      const equals = part.indexOf("=");
      const candidates = [colon, equals].filter((index) => index > 0);
      if (!candidates.length) return ["note", part];
      const splitAt = Math.min(...candidates);
      if (splitAt > 48) return ["note", part];
      return [part.slice(0, splitAt), part.slice(splitAt + 1)];
    }

    function renderPipeMemory(text) {
      const parts = String(text || "").trim().split("|").map((part) => part.trim()).filter(Boolean);
      const rows = parts.map((part) => {
        if (/^[★☆]+$/.test(part)) {
          return `
            <div class="structured-row">
              <div class="structured-key">importance</div>
              <div class="structured-value structured-stars">${escapeHtml(part)}</div>
            </div>
          `;
        }
        const [key, value] = splitStructuredPart(part);
        return `
          <div class="structured-row">
            <div class="structured-key">${escapeHtml(key)}</div>
            <div class="structured-value">${renderInlineMarkdown(value.trim() || part)}</div>
          </div>
        `;
      }).join("");
      return `<div class="structured-memory">${rows}</div>`;
    }

    function renderMarkdown(text) {
      const raw = String(text || "").replace(/\r\n/g, "\n");
      if (looksLikePipeMemory(raw)) return renderPipeMemory(raw);

      const lines = raw.split("\n");
      const blocks = [];
      let paragraph = [];
      let list = [];
      let listType = null;
      let quote = [];
      let code = [];
      let inCode = false;

      function flushParagraph() {
        if (paragraph.length) {
          blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
          paragraph = [];
        }
      }
      function flushList() {
        if (list.length) {
          const tag = listType === "ol" ? "ol" : "ul";
          blocks.push(`<${tag}>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${tag}>`);
          list = [];
          listType = null;
        }
      }
      function flushQuote() {
        if (quote.length) {
          blocks.push(`<blockquote>${quote.map((item) => `<p>${renderInlineMarkdown(item)}</p>`).join("")}</blockquote>`);
          quote = [];
        }
      }
      function flushTextBlocks() {
        flushParagraph();
        flushList();
        flushQuote();
      }

      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        const trimmed = line.trim();
        if (trimmed.startsWith("```")) {
          if (inCode) {
            blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
            code = [];
            inCode = false;
          } else {
            flushTextBlocks();
            inCode = true;
          }
          continue;
        }
        if (inCode) {
          code.push(line);
          continue;
        }
        if (!trimmed) {
          flushTextBlocks();
          continue;
        }
        const nextLine = lines[index + 1]?.trim() || "";
        if (trimmed.includes("|") && isMarkdownTableSeparator(nextLine)) {
          flushTextBlocks();
          const headers = splitTableRow(trimmed);
          const rows = [];
          index += 1;
          while (index + 1 < lines.length && lines[index + 1].trim().includes("|")) {
            index += 1;
            rows.push(splitTableRow(lines[index]));
          }
          blocks.push(renderTable(headers, rows));
          continue;
        }
        const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
        if (heading) {
          flushTextBlocks();
          const level = heading[1].length;
          blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
          continue;
        }
        if (/^---+$/.test(trimmed)) {
          flushTextBlocks();
          blocks.push("<hr>");
          continue;
        }
        const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
        const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
        if (bullet || ordered) {
          flushParagraph();
          flushQuote();
          const nextType = ordered ? "ol" : "ul";
          if (listType && listType !== nextType) flushList();
          listType = nextType;
          list.push((bullet || ordered)[1]);
          continue;
        }
        const quoted = /^>\s?(.+)$/.exec(trimmed);
        if (quoted) {
          flushParagraph();
          flushList();
          quote.push(quoted[1]);
          continue;
        }
        flushList();
        flushQuote();
        paragraph.push(trimmed);
      }
      if (inCode) blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      flushTextBlocks();
      return blocks.join("\n") || "<p></p>";
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) }
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      return data;
    }

    function filters() {
      const params = new URLSearchParams();
      const pairs = [
        ["wing", $("wingFilter").value],
        ["room", $("roomFilter").value],
        ["added_by", $("addedByFilter").value],
        ["contains", $("containsFilter").value.trim()],
      ];
      for (const [key, value] of pairs) if (value) params.set(key, value);
      params.set("limit", "80");
      return params;
    }

    function setOptions(select, entries, allLabel) {
      const current = select.value;
      select.innerHTML = `<option value="">${escapeHtml(allLabel)}</option>`;
      for (const [name, count] of entries) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = `${name} (${count})`;
        select.appendChild(option);
      }
      if ([...select.options].some((opt) => opt.value === current)) select.value = current;
    }

    function refreshRoomOptions() {
      const wing = $("wingFilter").value;
      const status = state.status || {};
      const rooms = wing ? (status.rooms_by_wing?.[wing] || {}) : (status.rooms || {});
      setOptions($("roomFilter"), Object.entries(rooms), "All rooms");
    }

    function renderStatus(status) {
      state.status = status;
      $("palacePath").textContent = status.palace_path;
      $("drawerCount").textContent = status.total_drawers;
      $("wingCount").textContent = Object.keys(status.wings || {}).length;
      setOptions($("wingFilter"), Object.entries(status.wings || {}), "All wings");
      setOptions($("addedByFilter"), Object.entries(status.added_by || {}), "Anyone");
      refreshRoomOptions();
    }

    function renderResults(drawers) {
      const box = $("results");
      box.innerHTML = "";
      if (!drawers.length) {
        box.innerHTML = '<div class="empty">No drawers found.</div>';
        return;
      }
      for (const drawer of drawers) {
        const button = document.createElement("button");
        button.className = "item" + (state.selected === drawer.drawer_id ? " active" : "");
        button.innerHTML = `
          <div class="path">${escapeHtml(drawer.wing)} <span class="muted">/</span> ${escapeHtml(drawer.room)}</div>
          <div class="meta-row">
            <span class="pill">${escapeHtml(drawer.source_file_name || drawer.source_file || "?")}</span>
            ${drawer.created_at ? `<span class="pill">${escapeHtml(formatDate(drawer.created_at))}</span>` : ""}
            ${drawer.similarity !== undefined ? `<span class="pill">sim ${escapeHtml(drawer.similarity)}</span>` : ""}
          </div>
          <div class="preview">${escapeHtml(drawer.content_preview || drawer.text || "")}</div>
        `;
        button.addEventListener("click", () => selectDrawer(drawer));
        box.appendChild(button);
      }
    }

    function renderDetail(drawer) {
      state.selected = drawer.drawer_id;
      $("detailTitle").textContent = `${drawer.wing || "unknown"} / ${drawer.room || "unknown"}`;
      $("detailSubtitle").textContent = drawer.drawer_id || "";
      const meta = drawer.metadata || {};
      const rows = [
        ["source", drawer.source_file || meta.source_file],
        ["created", formatDate(drawer.created_at || meta.filed_at)],
        ["added_by", drawer.added_by || meta.added_by],
        ["chunk", drawer.chunk_index ?? meta.chunk_index],
      ].filter(([, value]) => value !== undefined && value !== null && value !== "");
      $("detailMeta").innerHTML = rows.map(([k, v]) => `<div><span class="muted">${escapeHtml(k)}:</span> ${escapeHtml(v)}</div>`).join("");
      $("detailContent").innerHTML = renderMarkdown(drawer.content || drawer.text || "");
      document.querySelectorAll(".item").forEach((el) => el.classList.remove("active"));
    }

    async function selectDrawer(drawer) {
      if (drawer.drawer_id) {
        try {
          drawer = await api(`/api/drawers/${encodeURIComponent(drawer.drawer_id)}`);
        } catch {
          // Search results may not expose drawer ids yet; fall back to result payload.
        }
      }
      renderDetail(drawer);
    }

    async function loadList() {
      state.mode = "list";
      try {
        const data = await api(`/api/drawers?${filters().toString()}`);
        renderResults(data.drawers);
      } catch (err) {
        $("results").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
      }
    }

    async function runSearch() {
      const query = $("searchInput").value.trim();
      if (!query) return loadList();
      state.mode = "search";
      try {
        const data = await api("/api/search", {
          method: "POST",
          body: JSON.stringify({
            query,
            wing: $("wingFilter").value || null,
            room: $("roomFilter").value || null,
            limit: 25,
            max_distance: 1.5
          })
        });
        renderResults((data.results || []).map((hit) => ({
          ...hit,
          content_preview: hit.text,
          content: hit.text,
          source_file_name: hit.source_file,
        })));
      } catch (err) {
        $("results").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
      }
    }

    async function boot() {
      const status = await api("/api/status");
      renderStatus(status);
      await loadList();
    }

    $("wingFilter").addEventListener("change", () => { refreshRoomOptions(); loadList(); });
    $("roomFilter").addEventListener("change", loadList);
    $("addedByFilter").addEventListener("change", loadList);
    $("containsFilter").addEventListener("keydown", (e) => { if (e.key === "Enter") loadList(); });
    $("clearFilters").addEventListener("click", () => {
      $("wingFilter").value = "";
      $("roomFilter").value = "";
      $("addedByFilter").value = "";
      $("containsFilter").value = "";
      refreshRoomOptions();
      loadList();
    });
    $("searchButton").addEventListener("click", runSearch);
    $("searchInput").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });
    $("listButton").addEventListener("click", loadList);

    boot().catch((err) => {
      $("results").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    });
  </script>
</body>
</html>
"""
