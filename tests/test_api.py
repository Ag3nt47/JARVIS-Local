from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jarvis.config import Settings
from jarvis.providers import OllamaError


@pytest.mark.asyncio
async def test_app_logging_is_isolated_and_handlers_close(tmp_path: Path) -> None:
    from jarvis.api import create_app

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_settings = Settings(
        project_root=first_root,
        data_dir=first_root / "data",
        allowed_roots=(first_root,),
        quiet_hours_enabled=False,
    )
    second_settings = Settings(
        project_root=second_root,
        data_dir=second_root / "data",
        allowed_roots=(second_root,),
        quiet_hours_enabled=False,
    )
    first_settings.validate()
    second_settings.validate()
    first_app = create_app(first_settings)
    second_app = create_app(second_settings)

    async with (
        first_app.router.lifespan_context(first_app),
        second_app.router.lifespan_context(second_app),
    ):
        first_app.state.services.notifications.send_local("First", "first-only")
        second_app.state.services.notifications.send_local("Second", "second-only")

    first_log = first_settings.log_path.read_text(encoding="utf-8")
    second_log = second_settings.log_path.read_text(encoding="utf-8")
    assert "first-only" in first_log and "second-only" not in first_log
    assert "second-only" in second_log and "first-only" not in second_log
    assert first_app.state.logger.handlers == []
    assert second_app.state.logger.handlers == []

    moved = first_settings.log_path.with_suffix(".moved")
    first_settings.log_path.replace(moved)
    moved.replace(first_settings.log_path)


@pytest.mark.asyncio
async def test_api_routes_and_static_web_are_isolated(
    settings: Settings, monkeypatch
) -> None:
    # Importing the module is side-effect free; each test creates an isolated app instance.
    from jarvis import api as api_module

    web = settings.project_root / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<h1>isolated JARVIS web</h1>", encoding="utf-8")
    app = api_module.create_app(settings)
    services = app.state.services

    async def health() -> bool:
        return True

    async def list_models() -> list[dict[str, str]]:
        return [{"name": "test-chat"}]

    async def chat(messages, *, model=None, tools=None):
        return {
            "model": model or "test-chat",
            "done": True,
            "message": {"role": "assistant", "content": "mocked reply"},
        }

    async def embed(inputs, model=None):
        values = [inputs] if isinstance(inputs, str) else list(inputs)
        return [[1.0, 0.0] for _ in values]

    monkeypatch.setattr(services.provider, "health", health)
    monkeypatch.setattr(services.provider, "list_models", list_models)
    monkeypatch.setattr(services.provider, "chat", chat)
    monkeypatch.setattr(services.provider, "embed", embed)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/api/health")
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "healthy"
        assert health_response.json()["database"]["path"] == str(settings.database_path)
        assert health_response.json()["coding"]["approval_required"] is True

        models = await client.get("/api/models")
        assert models.json()["models"] == [{"name": "test-chat"}]
        assert (await client.get("/api/skills")).status_code == 200
        coding_status = await client.get("/api/coding/status")
        assert coding_status.status_code == 200
        assert "replace_text" in coding_status.json()["edit_tools"]
        live_status = await client.get("/api/live/status")
        assert live_status.status_code == 200
        assert live_status.json()["network_access"] == "fixed_public_rss_only"
        settings_response = await client.get("/api/settings")
        assert settings_response.status_code == 200
        assert settings_response.json()["settings"]["federation"]["transport"] == "tailscale"
        updated_settings = await client.patch(
            "/api/settings",
            json={
                "instance_name": "Test Node",
                "federation": {"compute_enabled": True, "admin_capable": True},
                "integrations": {"tailscale": True},
            },
        )
        assert updated_settings.status_code == 200
        assert updated_settings.json()["settings"]["instance_name"] == "Test Node"
        assert updated_settings.json()["settings"]["federation"]["require_target_approval"] is True

        action_catalog = await client.get("/api/actions/catalog")
        assert action_catalog.status_code == 200
        assert action_catalog.json()["shell_enabled"] is False
        assert {item["name"] for item in action_catalog.json()["actions"]} == {
            "list_windows",
            "read_clipboard",
            "click_control",
            "run_command",
        }
        queued_action = await client.post(
            "/api/actions/requests",
            json={
                "action": "run_command",
                "arguments": {
                    "executable": "python",
                    "arguments": ["--version"],
                    "cwd": str(settings.allowed_roots[0]),
                },
            },
        )
        assert queued_action.status_code == 200
        assert queued_action.json()["status"] == "approval_required"
        assert (
            await client.patch("/api/settings", json={"integrations": {"remote_shell": True}})
        ).status_code == 409

        created = await client.post(
            "/api/tasks",
            json={"title": "API task", "priority": 4, "dedupe_key": "api-task"},
        )
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        duplicate = await client.post(
            "/api/tasks", json={"title": "Duplicate", "dedupe_key": "api-task"}
        )
        assert duplicate.json()["created"] is False
        assert duplicate.json()["task"]["id"] == task_id
        assert (await client.get(f"/api/tasks/{task_id}")).status_code == 200
        updated = await client.patch(
            f"/api/tasks/{task_id}", json={"state": "in_progress", "priority": 5}
        )
        assert updated.json()["task"]["state"] == "in_progress"
        assert (await client.get("/api/tasks/missing")).status_code == 404
        assert (await client.get("/api/tasks?limit=0")).status_code == 422
        assert (await client.post("/api/tasks", json={"title": "", "extra": 1})).status_code == 422

        added_memory = await client.post(
            "/api/memory",
            json={"content": "remember this", "source": "test", "metadata": {"safe": True}},
        )
        assert added_memory.status_code == 201
        memory_id = added_memory.json()["item"]["id"]
        memory_search = await client.post(
            "/api/memory/search", json={"query": "remember", "min_score": 0.5}
        )
        assert memory_search.json()["results"][0]["id"] == memory_id
        assert (await client.delete(f"/api/memory/{memory_id}")).json() == {"deleted": True}
        assert (await client.delete(f"/api/memory/{memory_id}")).status_code == 404

        notification = await client.post(
            "/api/notifications",
            json={"title": "Local", "message": "Only here", "dedupe_key": "api-note"},
        )
        assert notification.status_code == 201
        assert notification.json()["notification"]["provider"] == "local_log"
        assert len((await client.get("/api/notifications")).json()["notifications"]) == 1

        node = await client.post(
            "/api/nodes/heartbeat",
            json={
                "id": "node-1",
                "name": "Test node",
                "url": "http://127.0.0.1:9001/",
                "capabilities": ["chat", "chat"],
            },
        )
        assert node.status_code == 200
        assert node.json()["node"]["capabilities"] == ["chat"]
        assert (await client.post(
            "/api/nodes/heartbeat",
            json={"id": "bad id", "name": "Bad", "url": "not-a-url"},
        )).status_code == 422

        chat_response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "skill_id": "executive_assistant",
                "use_tools": False,
            },
        )
        assert chat_response.status_code == 200
        assert chat_response.json()["message"] == "mocked reply"
        unknown_skill = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Hello"}], "skill_id": "missing"},
        )
        assert unknown_skill.status_code == 404

        target = settings.allowed_roots[0] / "from-api.txt"
        pending = await services.tools.execute(
            "write_file", {"path": str(target), "content": "approved", "overwrite": False}
        )
        request_id = pending["approval"]["id"]
        approved = await client.post(
            f"/api/approvals/{request_id}/approve", json={"actor": "tester"}
        )
        assert approved.status_code == 200
        assert target.read_text(encoding="utf-8") == "approved"
        assert (await client.post(
            f"/api/approvals/{request_id}/approve", json={"actor": "tester"}
        )).status_code == 409

        denied_pending = await services.tools.execute(
            "write_file",
            {
                "path": str(settings.allowed_roots[0] / "denied.txt"),
                "content": "no",
                "overwrite": False,
            },
        )
        denied_id = denied_pending["approval"]["id"]
        denied = await client.post(
            f"/api/approvals/{denied_id}/deny", json={"actor": "tester"}
        )
        assert denied.json()["approval"]["status"] == "denied"
        assert (settings.allowed_roots[0] / "denied.txt").exists() is False
        assert (await client.post(
            "/api/approvals/missing/deny", json={"actor": "tester"}
        )).status_code == 404

        audit_verification = await client.get("/api/audit/verify")
        assert audit_verification.json()["valid"] is True
        assert len((await client.get("/api/audit")).json()["entries"]) > 0
        assert (await client.get("/api/nodes")).json()["discovery_enabled"] is False
        assert (await client.get("/api")).json()["name"] == "JARVIS Local API"

        static = await client.get("/")
        assert static.status_code == 200
        assert "isolated JARVIS web" in static.text
        assert static.headers["cache-control"] == "no-store"

        async def failed_chat(messages, *, model=None, tools=None):
            raise OllamaError("mocked outage")

        monkeypatch.setattr(services.provider, "chat", failed_chat)
        unavailable = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "skill_id": "executive_assistant",
                "use_tools": False,
            },
        )
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "mocked outage", "service": "ollama"}

    await services.provider.close()
    services.database.close()
