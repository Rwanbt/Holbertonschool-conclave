"""Couche SQLite durable du Palier 4 — aucune logique métier ici.

- `aiosqlite` (pinned 0.22.1) : connexion async, requêtes paramétrées.
- `PRAGMA foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout`.
- Tables : schema_meta, analyses, expert_runs, tool_events, analysis_events,
  tool_states.
- L'initialisation est idempotente et se fait dans le lifespan FastAPI.
- Une analyse inachevée (running) trouvée au démarrage devient `interrupted`
  sans perte des résultats déjà persistés.
- `DISABLED_TOOLS` ne sert qu'à initialiser une base neuve : ensuite la table
  `tool_states` est la source de vérité.

Toute donnée JSON écrite ici provient de modèles Pydantic validés
(`model_dump(mode="json")`), jamais d'un texte LLM brut.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

SCHEMA_VERSION = "1"

SCHEMA_SQL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS analyses (
        id TEXT PRIMARY KEY,
        document TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        error_code TEXT,
        usage_json TEXT,
        verdict_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS expert_runs (
        id TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        status TEXT NOT NULL,
        output_json TEXT,
        error_code TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tool_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
        agent_role TEXT NOT NULL,
        llm_round INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        tool_name TEXT NOT NULL,
        status TEXT NOT NULL,
        input_summary_json TEXT NOT NULL,
        output_summary_json TEXT,
        duration_ms INTEGER NOT NULL,
        error_code TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS analysis_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_states (
        tool_name TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL
    )""",
)

TERMINAL_ANALYSIS_STATUSES = ("completed", "degraded", "failed", "interrupted")
TERMINAL_EVENTS = (
    "analysis.completed",
    "analysis.degraded",
    "analysis.failed",
    "analysis.interrupted",
)


def utc_now_iso() -> str:
    """Date UTC explicite au format ISO-8601 (p.ex. 2026-08-19T10:15:30+00:00)."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent(path: str) -> None:
    parent = Path(path).parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def open_connection(database_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Connexion configurée (row_factory, FK, WAL, busy_timeout), fermée à la sortie."""
    conn = aiosqlite.connect(database_path)
    async with conn as raw:
        raw.row_factory = aiosqlite.Row
        await raw.execute("PRAGMA foreign_keys = ON")
        await raw.execute("PRAGMA journal_mode = WAL")
        await raw.execute("PRAGMA busy_timeout = 5000")
        yield raw


async def initialize(database_path: str, disabled_tools: str = "") -> None:
    """Initialisation idempotente : schéma, version, reprise, états d'outils."""
    _ensure_parent(database_path)
    async with open_connection(database_path) as conn:
        for statement in SCHEMA_SQL:
            await conn.execute(statement)
        await conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        await _interrupt_running_analyses(conn)
        await _seed_tool_states_if_needed(conn, disabled_tools)
        await conn.commit()


async def _interrupt_running_analyses(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "UPDATE analyses SET status = 'interrupted', error_code = 'server_restart' "
        "WHERE status = 'running'"
    )
    rows = await (
        await conn.execute("SELECT id FROM analyses WHERE status = 'interrupted'")
    ).fetchall()
    for row in rows:
        await conn.execute(
            "INSERT OR IGNORE INTO analysis_events "
            "(analysis_id, event_type, payload_json, created_at) "
            "VALUES (?, 'analysis.interrupted', ?, ?)",
            (
                row["id"],
                '{"analysis_id": "' + row["id"] + '"}',
                utc_now_iso(),
            ),
        )


async def _seed_tool_states_if_needed(
    conn: aiosqlite.Connection, disabled_tools: str
) -> None:
    row = await (
        await conn.execute("SELECT COUNT(*) AS n FROM tool_states")
    ).fetchone()
    if row and row["n"] > 0:
        return
    disabled = {name.strip() for name in disabled_tools.split(",") if name.strip()}
    for name in (
        "measure_current_document",
        "find_security_indicators_in_current_document",
        "estimate_current_analysis_cost",
    ):
        await conn.execute(
            "INSERT OR IGNORE INTO tool_states (tool_name, enabled) VALUES (?, ?)",
            (name, 0 if name in disabled else 1),
        )


# ---------------------------------------------------------------------------
# Requêtes (toutes paramétrées)
# ---------------------------------------------------------------------------


async def create_analysis(
    conn: aiosqlite.Connection, analysis_id: str, document: str, now: str
) -> None:
    await conn.execute(
        "INSERT INTO analyses (id, document, status, created_at) VALUES (?, ?, 'running', ?)",
        (analysis_id, document, now),
    )
    await conn.commit()


async def get_analysis(conn: aiosqlite.Connection, analysis_id: str) -> aiosqlite.Row | None:
    cursor = await conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
    return await cursor.fetchone()


async def set_analysis_status(
    conn: aiosqlite.Connection,
    analysis_id: str,
    status: str,
    *,
    completed_at: str | None = None,
    error_code: str | None = None,
) -> None:
    await conn.execute(
        "UPDATE analyses SET status = ?, completed_at = COALESCE(?, completed_at), "
        "error_code = ? WHERE id = ?",
        (status, completed_at, error_code, analysis_id),
    )
    await conn.commit()


async def set_analysis_started(
    conn: aiosqlite.Connection, analysis_id: str, started_at: str
) -> None:
    await conn.execute(
        "UPDATE analyses SET started_at = ? WHERE id = ?", (started_at, analysis_id)
    )
    await conn.commit()


async def set_analysis_usage(
    conn: aiosqlite.Connection, analysis_id: str, usage_json: str
) -> None:
    await conn.execute(
        "UPDATE analyses SET usage_json = ? WHERE id = ?", (usage_json, analysis_id)
    )
    await conn.commit()


async def set_analysis_verdict(
    conn: aiosqlite.Connection, analysis_id: str, verdict_json: str
) -> None:
    await conn.execute(
        "UPDATE analyses SET verdict_json = ? WHERE id = ?", (verdict_json, analysis_id)
    )
    await conn.commit()


async def upsert_expert_run(
    conn: aiosqlite.Connection,
    run_id: str,
    analysis_id: str,
    role: str,
    status: str,
    *,
    output_json: str | None = None,
    error_code: str | None = None,
    started_at: str,
    completed_at: str | None = None,
) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO expert_runs "
        "(id, analysis_id, role, status, output_json, error_code, started_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            analysis_id,
            role,
            status,
            output_json,
            error_code,
            started_at,
            completed_at,
        ),
    )
    await conn.commit()


async def list_expert_runs(
    conn: aiosqlite.Connection, analysis_id: str
) -> list[aiosqlite.Row]:
    cursor = await conn.execute(
        "SELECT * FROM expert_runs WHERE analysis_id = ? ORDER BY started_at", (analysis_id,)
    )
    return list(await cursor.fetchall())


async def insert_analysis_event(
    conn: aiosqlite.Connection,
    analysis_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: str,
) -> int:
    import json

    cursor = await conn.execute(
        "INSERT INTO analysis_events (analysis_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (analysis_id, event_type, json.dumps(payload, ensure_ascii=False), now),
    )
    await conn.commit()
    return int(cursor.lastrowid)


async def finish_analysis(
    conn: aiosqlite.Connection,
    analysis_id: str,
    *,
    status: str,
    error_code: str | None,
    completed_at: str,
    usage_json: str,
    verdict_json: str | None,
    event_type: str,
    event_payload: dict[str, Any],
) -> int:
    """Termine une analyse atomiquement : statut + usage + verdict + événement
    terminal dans UNE transaction. Évite la fenêtre où le statut est terminal
    mais l'événement terminal pas encore persisté (le SSE ne doit jamais voir
    un statut terminal sans son événement)."""
    import json

    await conn.execute(
        "UPDATE analyses SET status = ?, completed_at = ?, error_code = ?, "
        "usage_json = ?, verdict_json = ? WHERE id = ?",
        (
            status,
            completed_at,
            error_code,
            usage_json,
            verdict_json,
            analysis_id,
        ),
    )
    cursor = await conn.execute(
        "INSERT INTO analysis_events (analysis_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (
            analysis_id,
            event_type,
            json.dumps(event_payload, ensure_ascii=False),
            completed_at,
        ),
    )
    await conn.commit()
    return int(cursor.lastrowid)


async def insert_tool_event(
    conn: aiosqlite.Connection,
    *,
    analysis_id: str,
    agent_role: str,
    llm_round: int,
    sequence: int,
    tool_name: str,
    status: str,
    input_summary_json: str,
    output_summary_json: str | None,
    duration_ms: int,
    error_code: str | None,
    now: str,
) -> None:
    await conn.execute(
        "INSERT INTO tool_events "
        "(analysis_id, agent_role, llm_round, sequence, tool_name, status, "
        " input_summary_json, output_summary_json, duration_ms, error_code, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            analysis_id,
            agent_role,
            llm_round,
            sequence,
            tool_name,
            status,
            input_summary_json,
            output_summary_json,
            duration_ms,
            error_code,
            now,
        ),
    )
    await conn.commit()


async def list_events_after(
    conn: aiosqlite.Connection, analysis_id: str, after_id: int = 0
) -> list[aiosqlite.Row]:
    cursor = await conn.execute(
        "SELECT * FROM analysis_events WHERE analysis_id = ? AND id > ? ORDER BY id",
        (analysis_id, after_id),
    )
    return list(await cursor.fetchall())


async def list_tool_states(conn: aiosqlite.Connection) -> list[aiosqlite.Row]:
    cursor = await conn.execute("SELECT tool_name, enabled FROM tool_states ORDER BY tool_name")
    return list(await cursor.fetchall())


async def get_tool_state(conn: aiosqlite.Connection, tool_name: str) -> aiosqlite.Row | None:
    cursor = await conn.execute(
        "SELECT tool_name, enabled FROM tool_states WHERE tool_name = ?", (tool_name,)
    )
    return await cursor.fetchone()


async def set_tool_state(
    conn: aiosqlite.Connection, tool_name: str, enabled: bool
) -> None:
    await conn.execute(
        "INSERT INTO tool_states (tool_name, enabled) VALUES (?, ?) "
        "ON CONFLICT(tool_name) DO UPDATE SET enabled = excluded.enabled",
        (tool_name, 1 if enabled else 0),
    )
    await conn.commit()