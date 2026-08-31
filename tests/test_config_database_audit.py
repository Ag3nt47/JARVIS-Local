from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.audit import AuditLog
from jarvis.config import Settings, load_settings
from jarvis.database import Database


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "jarvis.internal"])
def test_settings_reject_non_loopback_binding(tmp_path: Path, host: str) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    configured = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        allowed_roots=(root,),
        host=host,
    )

    with pytest.raises(ValueError, match="Non-loopback binding is disabled"):
        configured.validate()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_settings_accept_loopback_binding(tmp_path: Path, host: str) -> None:
    configured = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        allowed_roots=(tmp_path,),
        host=host,
    )

    configured.validate()

    assert configured.allowed_roots == (tmp_path.resolve(),)


def test_network_binding_cannot_bypass_local_only_mode(tmp_path: Path) -> None:
    configured = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        allowed_roots=(tmp_path,),
        host="0.0.0.0",
    )

    with pytest.raises(ValueError, match="Non-loopback binding is disabled"):
        configured.validate()


def test_load_settings_applies_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        "\n".join(
            [
                "[server]",
                'host = "0.0.0.0"',
                "port = 9000",
                "[security]",
                f'allowed_roots = ["{workspace.as_posix()}"]',
                "[ollama]",
                'base_url = "http://localhost:11434/"',
                'chat_model = "from-file"',
                'embed_model = "embed-file"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_HOST", "127.0.0.1")
    monkeypatch.setenv("JARVIS_PORT", "9876")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", "from-env")

    configured = load_settings(config_path)

    assert configured.host == "127.0.0.1"
    assert configured.port == 9876
    assert configured.data_dir == (tmp_path / "isolated-data").resolve()
    assert configured.ollama_base_url == "http://localhost:11434"
    assert configured.ollama_chat_model == "from-env"
    assert configured.ollama_embed_model == "embed-file"
    assert configured.allowed_roots == (workspace.resolve(),)


def test_explicit_missing_config_fails_closed(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing.toml"

    with pytest.raises(FileNotFoundError, match="JARVIS config file not found"):
        load_settings(missing)

    monkeypatch.setenv("JARVIS_CONFIG", str(missing))
    with pytest.raises(FileNotFoundError, match="JARVIS config file not found"):
        load_settings()


def test_database_files_are_isolated_and_transactions_roll_back(tmp_path: Path) -> None:
    first = Database(tmp_path / "first" / "jarvis.sqlite3")
    second = Database(tmp_path / "second" / "jarvis.sqlite3")
    first.initialize()
    second.initialize()
    try:
        first_audit = AuditLog(first, secret="first")
        first_audit.append("tester", "isolated", "database:first")

        assert len(first.query_all("SELECT * FROM audit_log")) == 1
        assert second.query_all("SELECT * FROM audit_log") == []

        with (
            pytest.raises(RuntimeError, match="force rollback"),
            second.transaction() as connection,
        ):
            connection.execute(
                """INSERT INTO nodes
                (id,name,url,capabilities_json,last_seen_at,status)
                VALUES ('rolled-back','node','http://node','[]','now','online')"""
            )
            raise RuntimeError("force rollback")
        assert second.query_all("SELECT * FROM nodes") == []
    finally:
        first.close()
        second.close()


def test_audit_hash_chain_detects_deliberate_sql_tamper(database: Database) -> None:
    audit = AuditLog(database, secret="chain-secret")
    first = audit.append("user", "task.created", "task:one", {"title": "One"})
    second = audit.append("user", "task.updated", "task:one", {"state": "done"})

    verification = audit.verify()
    assert verification == {
        "valid": True,
        "entries": 2,
        "head_hash": second["entry_hash"],
    }
    assert second["previous_hash"] == first["entry_hash"]

    database.execute(
        "UPDATE audit_log SET details_json=? WHERE id=?",
        ('{"title":"TAMPERED"}', first["id"]),
    )

    assert audit.verify() == {"valid": False, "entries": 2, "broken_at_id": first["id"]}
