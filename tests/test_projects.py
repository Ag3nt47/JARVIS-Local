"""Persistence, policy, audit, and API tests for local project metadata."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jarvis.audit import AuditLog
from jarvis.config import Settings
from jarvis.database import Database
from jarvis.services import ProjectService


def test_projects_are_empty_until_created_and_persist(
    database: Database, audit: AuditLog, settings: Settings
) -> None:
    projects = ProjectService(database, audit)
    assert projects.list() == []

    created = projects.create(
        "  Local Intelligence Upgrade  ",
        "Add bounded local reasoning and retrieval.",
        status="planned",
        priority=5,
        progress=15,
        next_action="Review the local model benchmark",
        tags=["Ollama", "ollama", "Security"],
        accent="violet",
        pinned=True,
        actor="tester",
    )

    assert created["title"] == "Local Intelligence Upgrade"
    assert created["summary"] == "Add bounded local reasoning and retrieval."
    assert created["next_action"] == "Review the local model benchmark"
    assert created["tags"] == ["Ollama", "Security"]
    assert created["pinned"] is True
    assert created["allowed_transitions"] == ["active", "archived", "completed"]
    assert projects.get(created["id"]) == created

    second_database = Database(settings.database_path)
    second_database.initialize()
    try:
        persisted = ProjectService(
            second_database, AuditLog(second_database, secret="unit-test-secret")
        )
        assert persisted.get(created["id"])["title"] == "Local Intelligence Upgrade"
    finally:
        second_database.close()

    table_columns = {row["name"] for row in database.query_all("PRAGMA table_info(projects)")}
    assert {"path", "command", "script", "executable"}.isdisjoint(table_columns)
    assert audit.verify()["valid"] is True


def test_project_updates_are_allowlisted_transitioned_and_audited(
    database: Database, audit: AuditLog
) -> None:
    projects = ProjectService(database, audit)
    first = projects.create("First", priority=2)
    second = projects.create("Pinned", priority=1, pinned=True)

    assert [project["id"] for project in projects.list()] == [second["id"], first["id"]]
    active = projects.update(
        first["id"],
        {
            "status": "active",
            "progress": 40,
            "next_action": "Finish the API contract",
            "tags": ["Backend", "API"],
        },
        actor="tester",
    )
    assert active["status"] == "active"
    assert active["progress"] == 40
    assert active["allowed_transitions"] == ["archived", "completed", "paused"]

    completed = projects.update(first["id"], {"status": "completed"}, actor="tester")
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert [item["id"] for item in projects.list("completed")] == [first["id"]]

    with pytest.raises(ValueError, match="100 percent"):
        projects.update(first["id"], {"progress": 50})
    archived = projects.update(first["id"], {"status": "archived"})
    assert archived["allowed_transitions"] == ["planned"]
    with pytest.raises(ValueError, match="cannot transition"):
        projects.update(first["id"], {"status": "active"})

    for field in ("path", "command", "script", "executable"):
        with pytest.raises(ValueError, match="Unsupported project fields"):
            projects.update(first["id"], {field: "unsafe"})
    with pytest.raises(ValueError, match="control characters"):
        projects.create("unsafe\nproject")
    with pytest.raises(ValueError, match="Project tags"):
        projects.create("Unsafe tag", tags=["../outside"])
    with pytest.raises(KeyError, match="Project not found"):
        projects.update("00000000-0000-4000-8000-000000000000", {"pinned": True})

    entries = list(reversed(audit.list()))
    assert [entry["action"] for entry in entries] == [
        "project.created",
        "project.created",
        "project.updated",
        "project.updated",
        "project.updated",
    ]
    assert entries[2]["details"] == {"fields": ["next_action", "progress", "status", "tags"]}
    assert audit.verify()["valid"] is True


@pytest.mark.asyncio
async def test_projects_api_is_strict_metadata_only(tmp_path: Path) -> None:
    from jarvis.api import create_app

    root = tmp_path / "project"
    root.mkdir()
    settings = Settings(
        project_root=root,
        data_dir=tmp_path / "data",
        allowed_roots=(root,),
        quiet_hours_enabled=False,
    )
    settings.validate()
    app = create_app(settings)
    services = app.state.services
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.get("/api/projects")
        assert empty.status_code == 200
        assert empty.json() == {"projects": []}

        created = await client.post(
            "/api/projects",
            json={
                "title": "JARVIS Local",
                "summary": "Build the private local assistant.",
                "status": "planned",
                "priority": 5,
                "progress": 25,
                "next_action": "Wire the project tabs",
                "tags": ["JARVIS", "Local AI"],
                "accent": "cyan",
                "pinned": True,
            },
        )
        assert created.status_code == 201
        project = created.json()["project"]
        project_id = project["id"]
        assert project["title"] == "JARVIS Local"
        assert project["allowed_transitions"] == ["active", "archived", "completed"]

        assert (await client.get(f"/api/projects/{project_id}")).json()["project"] == project
        listed = await client.get("/api/projects?status=planned&limit=1")
        assert listed.status_code == 200
        assert listed.json()["projects"][0]["id"] == project_id

        updated = await client.patch(
            f"/api/projects/{project_id}",
            json={
                "status": "active",
                "progress": 50,
                "next_action": "Validate the local-only flow",
                "pinned": False,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["project"]["status"] == "active"
        assert updated.json()["project"]["progress"] == 50
        assert updated.json()["project"]["pinned"] is False

        invalid_payloads = (
            {"title": "Unsafe", "path": "C:/Windows"},
            {"title": "Unsafe", "command": "whoami"},
            {"title": "Unsafe", "script": "print('no')"},
            {"title": "Unsafe", "tags": ["../escape"]},
        )
        for payload in invalid_payloads:
            assert (await client.post("/api/projects", json=payload)).status_code == 422

        assert (await client.patch(f"/api/projects/{project_id}", json={})).status_code == 422
        assert (
            await client.patch(f"/api/projects/{project_id}", json={"next_action": None})
        ).status_code == 422
        assert (await client.get("/api/projects?limit=0")).status_code == 422
        assert (await client.get("/api/projects?status=unknown")).status_code == 422
        assert (await client.get("/api/projects/not-a-uuid")).status_code == 422
        assert (
            await client.get("/api/projects/00000000-0000-4000-8000-000000000000")
        ).status_code == 404
        assert (
            await client.post(f"/api/projects/{project_id}/actions", json={})
        ).status_code == 404

        audit_entries = (await client.get("/api/audit")).json()["entries"]
        project_entries = [
            entry for entry in audit_entries if entry["resource"] == f"project:{project_id}"
        ]
        assert [entry["action"] for entry in reversed(project_entries)] == [
            "project.created",
            "project.updated",
        ]
        assert all(entry["actor"] == "user:hud" for entry in project_entries)

    await services.provider.close()
    services.federation.close()
    services.database.close()
