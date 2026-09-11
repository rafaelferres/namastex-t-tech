from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from application.tracing import QuoteAttempt
from infrastructure.persistence.attempts import SQLiteAttempts
from infrastructure.persistence.connection import connect
from interfaces.trace import main


def test_inspection_command_reads_cached_resolution(tmp_path: Path, capsys) -> None:
    path = tmp_path / "trace.sqlite"
    conn = connect(path)
    try:
        asyncio.run(
            SQLiteAttempts(conn).record(
                QuoteAttempt(
                    "trace-test",
                    "conv-test",
                    "hash",
                    0,
                    "quoted",
                    "cache",
                    None,
                    0,
                    False,
                    False,
                    None,
                    datetime(2026, 9, 11, tzinfo=UTC),
                )
            )
        )
    finally:
        conn.close()
    assert main(["trace-test", "--database", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Desfecho: quoted" in output and "origem=cache" in output
    assert "Tentativa" not in output


def test_inspection_missing_database_does_not_create_it(tmp_path: Path, capsys) -> None:
    path = tmp_path / "missing.sqlite"
    assert main(["trace-test", "--database", str(path)]) == 1
    assert not path.exists()
    assert "Não foi possível" in capsys.readouterr().err


def test_inspection_is_available_as_python_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "interfaces.trace", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "trace_id" in result.stdout
