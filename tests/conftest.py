from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.audit import AuditLog
from jarvis.config import Settings
from jarvis.database import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    project_root = tmp_path / "project"
    workspace = project_root / "workspace"
    workspace.mkdir(parents=True)
    configured = Settings(
        project_root=project_root,
        data_dir=tmp_path / "data",
        allowed_roots=(workspace,),
        ollama_base_url="http://127.0.0.1:11434",
        ollama_chat_model="test-chat",
        ollama_embed_model="test-embed",
        scheduler_interval_seconds=60,
        quiet_hours_start="22:00",
        quiet_hours_end="07:00",
    )
    configured.validate()
    return configured


@pytest.fixture
def database(settings: Settings) -> Database:
    database = Database(settings.database_path)
    database.initialize()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def audit(database: Database) -> AuditLog:
    return AuditLog(database, secret="unit-test-secret")
