from unittest.mock import patch

from mempalace.backends.base import GetResult
from mempalace.dashboard import DASHBOARD_HTML, DashboardApp, _where_filter


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
