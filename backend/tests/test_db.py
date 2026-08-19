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
            await db.create_analysis(conn, analysis_id, "contenu", db.utc_now_iso())

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