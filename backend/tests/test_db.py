"""Tests de la couche SQLite durable (Palier 4) — base temporaire par test."""

import asyncio

import pytest

from backend.app import db

SCHEMA_TABLES = {
    "schema_meta",
    "analyses",
    "expert_runs",
    "tool_events",
    "analysis_events",
    "tool_states",
    "analysis_tool_states",
    "analysis_security",
}


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "conclave_test.db")


def test_schema_creation_is_idempotent(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))
    asyncio.run(db.initialize(temp_db, ""))

    async def check():
        async with db.open_connection(temp_db) as conn:
            rows = await (
                await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ).fetchall()
        return {row["name"] for row in rows}

    assert asyncio.run(check()) == SCHEMA_TABLES


def test_foreign_keys_enabled(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))

    async def check():
        async with db.open_connection(temp_db) as conn:
            row = await (await conn.execute("PRAGMA foreign_keys")).fetchone()
        return row[0]

    assert asyncio.run(check()) == 1


def test_persistence_after_reopen(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))
    analysis_id = "analysis-1"

    async def create():
        async with db.open_connection(temp_db) as conn:
            await db.create_analysis(conn, analysis_id, "contenu", db.utc_now_iso())
            await db.set_analysis_status(conn, analysis_id, "completed", completed_at=db.utc_now_iso())

    asyncio.run(create())

    async def read_after_reopen():
        async with db.open_connection(temp_db) as conn:
            row = await db.get_analysis(conn, analysis_id)
        assert row is not None
        assert row["status"] == "completed"
        assert row["document"] == "contenu"

    asyncio.run(read_after_reopen())


def test_running_analyses_become_interrupted_at_restart(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))
    analysis_id = "analysis-running"

    async def create():
        async with db.open_connection(temp_db) as conn:
            await db.create_analysis(
                conn, analysis_id, "contenu", db.utc_now_iso(), status="running"
            )

    asyncio.run(create())

    # « Redémarrage » : réinitialisation du schéma.
    asyncio.run(db.initialize(temp_db, ""))

    async def check():
        async with db.open_connection(temp_db) as conn:
            row = await db.get_analysis(conn, analysis_id)
            events = await db.list_events_after(conn, analysis_id)
        assert row["status"] == "interrupted"
        assert row["error_code"] == "server_restart"
        assert any(e["event_type"] == "analysis.interrupted" for e in events)

    asyncio.run(check())


def test_queued_analyses_stay_queued_at_restart(temp_db) -> None:
    """Une analyse `queued` n'a jamais démarré de tâche de fond : un
    redémarrage ne doit pas la marquer `interrupted`, elle peut toujours
    être démarrée après rechargement du navigateur."""
    asyncio.run(db.initialize(temp_db, ""))
    analysis_id = "analysis-queued"

    async def create():
        async with db.open_connection(temp_db) as conn:
            await db.create_analysis(conn, analysis_id, "contenu", db.utc_now_iso())

    asyncio.run(create())
    asyncio.run(db.initialize(temp_db, ""))

    async def check():
        async with db.open_connection(temp_db) as conn:
            row = await db.get_analysis(conn, analysis_id)
        assert row["status"] == "queued"

    asyncio.run(check())


def test_tool_states_seeded_and_persist(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, "measure_current_document"))

    async def check_seed():
        async with db.open_connection(temp_db) as conn:
            states = await db.list_tool_states(conn)
        by_name = {row["tool_name"]: row["enabled"] for row in states}
        assert len(by_name) == 3
        assert by_name["measure_current_document"] == 0
        assert by_name["find_security_indicators_in_current_document"] == 1
        assert by_name["estimate_current_analysis_cost"] == 1

    asyncio.run(check_seed())

    # DISABLED_TOOLS ne réinitialise pas une base existante (source de vérité : tool_states).
    asyncio.run(db.initialize(temp_db, ""))

    async def check_not_reseeded():
        async with db.open_connection(temp_db) as conn:
            states = await db.list_tool_states(conn)
        by_name = {row["tool_name"]: row["enabled"] for row in states}
        assert by_name["measure_current_document"] == 0

    asyncio.run(check_not_reseeded())


def test_set_tool_state_is_idempotent_and_persists(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))

    async def flip():
        async with db.open_connection(temp_db) as conn:
            await db.set_tool_state(conn, "measure_current_document", False)
            await db.set_tool_state(conn, "measure_current_document", False)
            row = await db.get_tool_state(conn, "measure_current_document")
        assert row["enabled"] == 0

    asyncio.run(flip())

    # Survit à la réouverture.
    async def check():
        async with db.open_connection(temp_db) as conn:
            row = await db.get_tool_state(conn, "measure_current_document")
        assert row["enabled"] == 0

    asyncio.run(check())


def test_tool_event_rows_are_ordered_and_bounded(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))

    async def write():
        async with db.open_connection(temp_db) as conn:
            await db.create_analysis(conn, "a1", "doc", db.utc_now_iso())
            await db.insert_tool_event(
                conn,
                analysis_id="a1",
                agent_role="comptable",
                llm_round=1,
                sequence=1,
                tool_name="measure_current_document",
                status="success",
                input_summary_json='{"tool": "measure_current_document"}',
                output_summary_json='{"character_count": 5}',
                duration_ms=3,
                error_code=None,
                now=db.utc_now_iso(),
            )

    asyncio.run(write())

    async def read():
        async with db.open_connection(temp_db) as conn:
            cursor = await conn.execute(
                "SELECT * FROM tool_events WHERE analysis_id = 'a1' ORDER BY id"
            )
            rows = list(await cursor.fetchall())
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "measure_current_document"
        assert rows[0]["status"] == "success"
        assert rows[0]["duration_ms"] == 3

    asyncio.run(read())


def test_partial_tool_states_are_completed_without_overwrite(temp_db) -> None:
    """Une base ancienne/partielle (une seule ligne `tool_states`) doit être
    complétée par les outils manquants, sans jamais écraser la ligne
    existante — même si `DISABLED_TOOLS` la contredirait."""
    asyncio.run(db.initialize(temp_db, ""))

    async def leave_only_one_row():
        async with db.open_connection(temp_db) as conn:
            await conn.execute(
                "DELETE FROM tool_states WHERE tool_name != 'measure_current_document'"
            )
            await conn.execute(
                "UPDATE tool_states SET enabled = 0 WHERE tool_name = 'measure_current_document'"
            )
            await conn.commit()

    asyncio.run(leave_only_one_row())

    # Réinitialisation avec un DISABLED_TOOLS qui viserait la ligne existante :
    # elle ne doit pas être réécrite (source de vérité = ligne déjà persistée).
    asyncio.run(db.initialize(temp_db, "estimate_current_analysis_cost"))

    async def check():
        async with db.open_connection(temp_db) as conn:
            states = await db.list_tool_states(conn)
        by_name = {row["tool_name"]: row["enabled"] for row in states}
        assert len(by_name) == 3
        # Ligne préexistante conservée telle quelle (désactivée par un choix
        # utilisateur antérieur, pas par le DISABLED_TOOLS de ce redémarrage).
        assert by_name["measure_current_document"] == 0
        # Lignes manquantes complétées selon DISABLED_TOOLS de CE redémarrage.
        assert by_name["estimate_current_analysis_cost"] == 0
        assert by_name["find_security_indicators_in_current_document"] == 1

    asyncio.run(check())


def test_snapshot_analysis_tool_states_is_immutable(temp_db) -> None:
    """La configuration figée à la création d'une analyse ne doit plus
    changer même si le registre global change ensuite."""
    asyncio.run(db.initialize(temp_db, ""))

    async def create_and_snapshot():
        async with db.open_connection(temp_db) as conn:
            await db.create_analysis(conn, "a1", "doc", db.utc_now_iso())
            rows = await db.snapshot_analysis_tool_states(conn, "a1")
        assert {r["tool_name"] for r in rows} == {
            "measure_current_document",
            "find_security_indicators_in_current_document",
            "estimate_current_analysis_cost",
        }
        assert all(r["enabled"] for r in rows)

    asyncio.run(create_and_snapshot())

    async def disable_globally_then_reread():
        async with db.open_connection(temp_db) as conn:
            await db.set_tool_state(conn, "measure_current_document", False)
            allowed = await db.get_analysis_allowed_tools(conn, "a1")
        # Le registre global a changé, la configuration figée de a1 n'a pas bougé.
        assert "measure_current_document" in allowed

    asyncio.run(disable_globally_then_reread())


def test_start_analysis_is_a_single_winner_compare_and_set(temp_db) -> None:
    asyncio.run(db.initialize(temp_db, ""))

    async def create():
        async with db.open_connection(temp_db) as conn:
            await db.create_analysis(conn, "a1", "doc", db.utc_now_iso())

    asyncio.run(create())

    async def race():
        async with db.open_connection(temp_db) as conn:
            first = await db.start_analysis(conn, "a1", db.utc_now_iso())
            second = await db.start_analysis(conn, "a1", db.utc_now_iso())
            row = await db.get_analysis(conn, "a1")
        assert first is True
        assert second is False
        assert row["status"] == "running"
        assert row["started_at"] is not None

    asyncio.run(race())