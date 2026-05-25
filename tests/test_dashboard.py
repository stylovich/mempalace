import errno
from unittest.mock import patch

import pytest

from mempalace.backends.base import GetResult
from mempalace.dashboard import DASHBOARD_HTML, DashboardApp, serve_dashboard, stop_dashboard, _where_filter


class FakeCollection:
    def __init__(self):
        self.calls = []
        self.rows = GetResult(
            ids=["drawer_a", "drawer_b"],
            documents=["Alpha memory text", "Beta memory text"],
            metadatas=[
                {
                    "wing": "project",
                    "room": "backend",
                    "source_file": "/tmp/auth.py",
                    "added_by": "codex",
                    "filed_at": "2026-05-01T00:00:00",
                    "chunk_index": 0,
                },
                {
                    "wing": "notes",
                    "room": "planning",
                    "source_file": "/tmp/plan.md",
                    "added_by": "miner",
                    "filed_at": "2026-05-02T00:00:00",
                    "chunk_index": 1,
                },
            ],
        )

    def count(self):
        return 2

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        if kwargs.get("ids") == ["drawer_a"]:
            return GetResult(
                ids=[self.rows.ids[0]],
                documents=[self.rows.documents[0]],
                metadatas=[self.rows.metadatas[0]],
            )
        return self.rows

    def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))


def test_where_filter_combines_optional_filters():
    assert _where_filter() == {}
    assert _where_filter(wing="project") == {"wing": "project"}
    assert _where_filter(wing="project", room="backend", added_by="codex") == {
        "$and": [{"wing": "project"}, {"room": "backend"}, {"added_by": "codex"}]
    }


def test_dashboard_status_counts_facets():
    fake = FakeCollection()
    app = DashboardApp("/tmp/palace")

    with patch("mempalace.dashboard.get_collection", return_value=fake):
        status = app.status()

    assert status["total_drawers"] == 2
    assert status["wings"] == {"notes": 1, "project": 1}
    assert status["rooms"] == {"backend": 1, "planning": 1}
    assert status["rooms_by_wing"]["project"] == {"backend": 1}
    assert status["added_by"] == {"codex": 1, "miner": 1}


def test_dashboard_list_drawers_applies_filters_and_previews():
    fake = FakeCollection()
    app = DashboardApp("/tmp/palace")
    params = {
        "wing": ["project"],
        "room": ["backend"],
        "contains": ["Alpha"],
        "limit": ["25"],
        "offset": ["5"],
    }

    with patch("mempalace.dashboard.get_collection", return_value=fake):
        result = app.list_drawers(params)

    _, kwargs = fake.calls[-1]
    assert kwargs["where"] == {"$and": [{"wing": "project"}, {"room": "backend"}]}
    assert kwargs["where_document"] == {"$contains": "Alpha"}
    assert kwargs["limit"] == 25
    assert kwargs["offset"] == 5
    assert result["drawers"][0]["drawer_id"] == "drawer_a"
    assert result["drawers"][0]["source_file_name"] == "auth.py"
    assert result["drawers"][0]["content_preview"] == "Alpha memory text"


def test_dashboard_get_drawer_returns_full_drawer():
    fake = FakeCollection()
    app = DashboardApp("/tmp/palace")

    with patch("mempalace.dashboard.get_collection", return_value=fake):
        drawer = app.get_drawer("drawer_a")

    assert drawer["drawer_id"] == "drawer_a"
    assert drawer["content"] == "Alpha memory text"
    assert drawer["metadata"]["source_file_name"] == "auth.py"


def test_dashboard_status_exposes_write_mode():
    fake = FakeCollection()
    app = DashboardApp("/tmp/palace", write_enabled=True)

    with patch("mempalace.dashboard.get_collection", return_value=fake):
        status = app.status()

    assert status["write_enabled"] is True


def test_dashboard_update_requires_write_mode():
    app = DashboardApp("/tmp/palace")

    with patch("mempalace.dashboard.get_collection", return_value=FakeCollection()):
        try:
            app.update_drawer("drawer_a", {"content": "Updated"})
        except PermissionError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("update should require write mode")


def test_dashboard_update_upserts_content_and_metadata():
    fake = FakeCollection()
    app = DashboardApp("/tmp/palace", write_enabled=True)

    with patch("mempalace.dashboard.get_collection", return_value=fake):
        app.update_drawer(
            "drawer_a",
            {
                "content": "Updated memory text",
                "wing": "new-wing",
                "room": "new-room",
                "source_file": "/tmp/new.md",
                "added_by": "tester",
            },
        )

    _, kwargs = fake.calls[-2]
    assert kwargs["ids"] == ["drawer_a"]
    assert kwargs["documents"] == ["Updated memory text"]
    assert kwargs["metadatas"][0]["wing"] == "new-wing"
    assert kwargs["metadatas"][0]["room"] == "new-room"
    assert kwargs["metadatas"][0]["source_file"] == "/tmp/new.md"
    assert kwargs["metadatas"][0]["added_by"] == "tester"
    assert kwargs["metadatas"][0]["filed_at"] == "2026-05-01T00:00:00"
    assert "source_file_name" not in kwargs["metadatas"][0]


def test_dashboard_delete_requires_write_mode():
    app = DashboardApp("/tmp/palace")

    with patch("mempalace.dashboard.get_collection", return_value=FakeCollection()):
        try:
            app.delete_drawer("drawer_a")
        except PermissionError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("delete should require write mode")


def test_dashboard_delete_removes_existing_drawer():
    fake = FakeCollection()
    app = DashboardApp("/tmp/palace", write_enabled=True)

    with patch("mempalace.dashboard.get_collection", return_value=fake):
        result = app.delete_drawer("drawer_a")

    assert result == {"deleted": "drawer_a"}
    assert fake.calls[-1] == ("delete", {"ids": ["drawer_a"]})


def test_dashboard_search_delegates_to_search_memories():
    app = DashboardApp("/tmp/palace", collection_name="custom")

    with patch("mempalace.dashboard.search_memories", return_value={"results": []}) as search:
        result = app.search(
            {
                "query": "auth",
                "wing": "project",
                "room": "backend",
                "limit": 7,
                "max_distance": 1.5,
            }
        )

    assert result == {"results": []}
    search.assert_called_once_with(
        query="auth",
        palace_path="/tmp/palace",
        wing="project",
        room="backend",
        n_results=7,
        max_distance=1.5,
        collection_name="custom",
    )


def test_dashboard_html_supports_markdown_tables_and_pipe_memories():
    assert "function renderTable" in DASHBOARD_HTML
    assert "function renderPipeMemory" in DASHBOARD_HTML
    assert "function splitEmbeddedSections" in DASHBOARD_HTML
    assert "function expandStructuredParts" in DASHBOARD_HTML
    assert "startsWithDate" in DASHBOARD_HTML
    assert 'return ["date", part]' in DASHBOARD_HTML
    assert "structured-memory" in DASHBOARD_HTML


def test_dashboard_html_keeps_selected_item_active():
    assert "button.dataset.drawerId" in DASHBOARD_HTML
    assert "function syncSelectedItem" in DASHBOARD_HTML
    assert 'el.classList.toggle("active", el.dataset.drawerId === state.selected)' in DASHBOARD_HTML


def test_dashboard_html_keeps_filter_sidebar_visible():
    assert "position: sticky;" in DASHBOARD_HTML
    assert "height: 100vh;" in DASHBOARD_HTML
    assert "overflow: auto;" in DASHBOARD_HTML


def test_serve_dashboard_reports_busy_port_without_traceback():
    with patch("mempalace.dashboard.DashboardServer", side_effect=OSError(errno.EADDRINUSE, "busy")):
        with pytest.raises(SystemExit) as exc:
            serve_dashboard(
                palace_path="/fake/palace",
                host="127.0.0.1",
                port=8765,
                open_browser=False,
            )

    message = str(exc.value)
    assert "Dashboard address already in use: 127.0.0.1:8765" in message
    assert "--port 0" in message


def test_stop_dashboard_reports_missing_process():
    with patch("mempalace.dashboard._find_dashboard_pid", return_value=(None, None)):
        with pytest.raises(SystemExit) as exc:
            stop_dashboard(host="127.0.0.1", port=8765)

    assert "No MemPalace dashboard appears to be running on 127.0.0.1:8765" in str(exc.value)


def test_stop_dashboard_sends_sigterm_to_pid(tmp_path):
    pid_path = tmp_path / "dashboard.pid"
    pid_path.write_text("123\n", encoding="utf-8")

    with (
        patch("mempalace.dashboard._find_dashboard_pid", return_value=(123, pid_path)),
        patch("mempalace.dashboard.os.kill") as kill,
        patch("mempalace.dashboard._process_exists", return_value=False),
    ):
        assert stop_dashboard(host="127.0.0.1", port=8765) is True

    kill.assert_called_once()
    assert not pid_path.exists()
