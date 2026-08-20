"""Couche SQLite durable du Palier 4 — aucune logique métier ici.

- `aiosqlite` (pinned 0.22.1) : connexion async, requêtes paramétrées.
- `PRAGMA foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout`.
- Tables : schema_meta, analyses, expert_runs, tool_events, analysis_events,
  tool_states, analysis_tool_states et analysis_security (v2).
- L'initialisation est idempotente et se fait dans le lifespan FastAPI.
- Une analyse `running` trouvée au démarrage devient `interrupted` sans perte
  des résultats déjà persistés ; une analyse `queued` reste `queued` (elle
  n'a jamais démarré de tâche de fond, un rechargement peut la démarrer).
- `DISABLED_TOOLS` ne sert qu'à initialiser une base neuve : ensuite la table
  `tool_states` (registre global, pour la prochaine analyse) est la source
  de vérité. `analysis_tool_states` fige, pour une analyse déjà créée, la
  configuration lue dans `tool_states` au moment de sa création : elle seule
  fait foi pour cette analyse, y compris si le registre global change ensuite.

Toute donnée JSON écrite ici provient de modèles Pydantic validés
(`model_dump(mode="json")`), jamais d'un texte LLM brut.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

SCHEMA_VERSION = "2"

KNOWN_TOOL_NAMES: tuple[str, ...] = (
    "measure_current_document",
    "find_security_indicators_in_current_document",
    "estimate_current_analysis_cost",
)

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
    """CREATE TABLE IF NOT EXISTS analysis_tool_states (
        analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
        tool_name TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        PRIMARY KEY (analysis_id, tool_name)
    )""",
    # Signaux d'injection repérés à la soumission, conservés avec l'analyse
    # pour rester consultables après un rechargement (observabilité).
    """CREATE TABLE IF NOT EXISTS analysis_security (
        analysis_id TEXT PRIMARY KEY REFERENCES analyses(id) ON DELETE CASCADE,
        signals_json TEXT NOT NULL
    )""",
)

TERMINAL_ANALYSIS_STATUSES = ("completed", "degraded", "failed", "interrupted")
TERMINAL_EVENTS = (
    "analysis.completed",
    "analysis.degraded",
    "analysis.failed",
    "analysis.interrupted",
)


class ActiveAnalysisLimitReached(RuntimeError):
    """La limite a été vérifiée sous verrou d'écriture SQLite."""

    def __init__(self, active: int) -> None:
        super().__init__(f"active analysis limit reached: {active}")
        self.active = active


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
        await _sync_tool_states(conn, disabled_tools)
        await conn.commit()


async def _interrupt_running_analyses(conn: aiosqlite.Connection) -> None:
    """Une analyse `running` (tâche de fond perdue au redémarrage) devient
    `interrupted`. Une analyse `queued` reste `queued` : elle n'a jamais
    démarré de tâche de fond, un `/start` après rechargement suffit."""
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


async def _sync_tool_states(conn: aiosqlite.Connection, disabled_tools: str) -> None:
    """Backfill idempotent : chaque outil connu obtient une ligne s'il n'en a
    pas déjà une. `INSERT OR IGNORE` ne touche jamais une ligne existante :
    une base ancienne ou partielle est complétée sans écraser les choix déjà
    persistés d'un utilisateur. `disabled_tools` ne sert qu'à l'état initial
    d'une ligne neuve."""
    disabled = {name.strip() for name in disabled_tools.split(",") if name.strip()}
    for name in KNOWN_TOOL_NAMES:
        await conn.execute(
            "INSERT OR IGNORE INTO tool_states (tool_name, enabled) VALUES (?, ?)",
            (name, 0 if name in disabled else 1),
        )


# ---------------------------------------------------------------------------
# Requêtes (toutes paramétrées)
# ---------------------------------------------------------------------------


async def create_analysis(
    conn: aiosqlite.Connection,
    analysis_id: str,
    document: str,
    now: str,
    *,
    status: str = "queued",
) -> None:
    """Crée l'analyse. Par défaut `queued` (Palier R1) : la tâche de fond
    n'est lancée qu'après `start_analysis`, jamais ici."""
    await conn.execute(
        "INSERT INTO analyses (id, document, status, created_at) VALUES (?, ?, ?, ?)",
        (analysis_id, document, status, now),
    )
    await conn.commit()


async def snapshot_analysis_tool_states(
    conn: aiosqlite.Connection, analysis_id: str
) -> list[aiosqlite.Row]:
    """Copie `tool_states` (registre global) vers `analysis_tool_states` pour
    cette analyse, dans la transaction courante, puis renvoie les lignes
    copiées. À appeler une seule fois, à la création de l'analyse : la
    configuration devient ensuite immuable pour cette analyse."""
    await conn.execute(
        "INSERT INTO analysis_tool_states (analysis_id, tool_name, enabled) "
        "SELECT ?, tool_name, enabled FROM tool_states",
        (analysis_id,),
    )
    await conn.commit()
    return await list_analysis_tool_states(conn, analysis_id)


async def count_active_analyses(conn: aiosqlite.Connection) -> int:
    """Analyses non terminées (queued ou running), pour borner la charge."""
    cursor = await conn.execute(
        "SELECT COUNT(*) AS n FROM analyses WHERE status IN ('queued', 'running')"
    )
    row = await cursor.fetchone()
    return int(row["n"]) if row else 0


async def create_queued_analysis(
    conn: aiosqlite.Connection,
    *,
    analysis_id: str,
    document: str,
    now: str,
    signals: list[str],
    max_active: int,
    queued_ttl_seconds: int,
) -> list[aiosqlite.Row]:
    """Crée tout l'état initial dans UNE transaction sérialisée.

    Le verrou ``BEGIN IMMEDIATE`` rend atomiques le nettoyage des anciennes
    files, le contrôle de concurrence, l'analyse, le snapshot des outils, le
    rapport sécurité et ``analysis.created``. Aucun demi-objet ne peut rester
    en base si une écriture échoue.
    """
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cutoff = (
            datetime.fromisoformat(now) - timedelta(seconds=queued_ttl_seconds)
        ).isoformat()
        stale = await (
            await conn.execute(
                "SELECT id FROM analyses "
                "WHERE status = 'queued' AND created_at < ?",
                (cutoff,),
            )
        ).fetchall()
        for row in stale:
            await conn.execute(
                "UPDATE analyses SET status = 'failed', completed_at = ?, "
                "error_code = 'start_timeout' WHERE id = ? AND status = 'queued'",
                (now, row["id"]),
            )
            await conn.execute(
                "INSERT INTO analysis_events "
                "(analysis_id, event_type, payload_json, created_at) "
                "VALUES (?, 'analysis.failed', ?, ?)",
                (
                    row["id"],
                    json.dumps(
                        {
                            "analysis_id": row["id"],
                            "status": "failed",
                            "error_code": "start_timeout",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )

        active = await count_active_analyses(conn)
        if active >= max_active:
            # Les expirations éventuelles sont utiles même si cette nouvelle
            # soumission est refusée. Le verrou reste tenu jusqu'ici.
            await conn.commit()
            raise ActiveAnalysisLimitReached(active)

        await conn.execute(
            "INSERT INTO analyses (id, document, status, created_at) "
            "VALUES (?, ?, 'queued', ?)",
            (analysis_id, document, now),
        )
        await conn.execute(
            "INSERT INTO analysis_tool_states (analysis_id, tool_name, enabled) "
            "SELECT ?, tool_name, enabled FROM tool_states",
            (analysis_id,),
        )
        rows = await list_analysis_tool_states(conn, analysis_id)
        enabled_tools = [row["tool_name"] for row in rows if row["enabled"]]
        disabled_tools = [row["tool_name"] for row in rows if not row["enabled"]]
        await conn.execute(
            "INSERT INTO analysis_security (analysis_id, signals_json) VALUES (?, ?)",
            (analysis_id, json.dumps(signals, ensure_ascii=False)),
        )
        await conn.execute(
            "INSERT INTO analysis_events "
            "(analysis_id, event_type, payload_json, created_at) "
            "VALUES (?, 'analysis.created', ?, ?)",
            (
                analysis_id,
                json.dumps(
                    {
                        "analysis_id": analysis_id,
                        "created_at": now,
                        "enabled_tools": enabled_tools,
                        "disabled_tools": disabled_tools,
                        "security_signals": signals,
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        await conn.commit()
        return rows
    except ActiveAnalysisLimitReached:
        raise
    except Exception:
        await conn.rollback()
        raise


async def set_analysis_security(
    conn: aiosqlite.Connection, analysis_id: str, signals: list[str]
) -> None:
    import json

    await conn.execute(
        "INSERT INTO analysis_security (analysis_id, signals_json) VALUES (?, ?) "
        "ON CONFLICT(analysis_id) DO UPDATE SET signals_json = excluded.signals_json",
        (analysis_id, json.dumps(signals, ensure_ascii=False)),
    )
    await conn.commit()


async def get_analysis_security(
    conn: aiosqlite.Connection, analysis_id: str
) -> list[str]:
    import json

    cursor = await conn.execute(
        "SELECT signals_json FROM analysis_security WHERE analysis_id = ?",
        (analysis_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return []
    try:
        parsed = json.loads(row["signals_json"])
    except (ValueError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


async def list_analysis_tool_states(
    conn: aiosqlite.Connection, analysis_id: str
) -> list[aiosqlite.Row]:
    cursor = await conn.execute(
        "SELECT tool_name, enabled FROM analysis_tool_states "
        "WHERE analysis_id = ? ORDER BY tool_name",
        (analysis_id,),
    )
    return list(await cursor.fetchall())


async def get_analysis_allowed_tools(
    conn: aiosqlite.Connection, analysis_id: str
) -> frozenset[str]:
    """Noms des outils activés dans la configuration figée de l'analyse."""
    rows = await list_analysis_tool_states(conn, analysis_id)
    return frozenset(row["tool_name"] for row in rows if row["enabled"])


async def start_analysis(
    conn: aiosqlite.Connection, analysis_id: str, started_at: str
) -> bool:
    """Transition atomique `queued` -> `running` (compare-and-set SQL).

    Renvoie True seulement si CETTE transaction a effectué la transition
    (donc doit lancer la tâche de fond) ; False si l'analyse est déjà
    `running`/terminale ou n'existe pas."""
    cursor = await conn.execute(
        "UPDATE analyses SET status = 'running', started_at = ? "
        "WHERE id = ? AND status = 'queued'",
        (started_at, analysis_id),
    )
    transitioned = cursor.rowcount == 1
    if transitioned:
        await conn.execute(
            "INSERT INTO analysis_events "
            "(analysis_id, event_type, payload_json, created_at) "
            "VALUES (?, 'analysis.started', ?, ?)",
            (
                analysis_id,
                json.dumps(
                    {"analysis_id": analysis_id, "started_at": started_at},
                    ensure_ascii=False,
                ),
                started_at,
            ),
        )
    await conn.commit()
    return transitioned


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


async def timeout_running_expert_runs(
    conn: aiosqlite.Connection,
    analysis_id: str,
    completed_at: str,
    error_code: str,
) -> list[str]:
    """Ferme les runs encore actifs dans la transaction de fin globale."""
    rows = await (
        await conn.execute(
            "SELECT role FROM expert_runs "
            "WHERE analysis_id = ? AND status = 'running'",
            (analysis_id,),
        )
    ).fetchall()
    roles = [str(row["role"]) for row in rows]
    await conn.execute(
        "UPDATE expert_runs SET status = 'timeout', error_code = ?, completed_at = ? "
        "WHERE analysis_id = ? AND status = 'running'",
        (error_code, completed_at, analysis_id),
    )
    for role in roles:
        await conn.execute(
            "INSERT INTO analysis_events "
            "(analysis_id, event_type, payload_json, created_at) "
            "VALUES (?, 'expert.timeout', ?, ?)",
            (
                analysis_id,
                json.dumps(
                    {
                        "analysis_id": analysis_id,
                        "role": role,
                        "error_code": error_code,
                    },
                    ensure_ascii=False,
                ),
                completed_at,
            ),
        )
    return roles


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
