"""Isolated PostgreSQL/pgvector infrastructure spike tests."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Iterable

import psycopg
import pytest

SPIKE_DATABASE_URL_ENV = "EVIDENCEFORGE_PGVECTOR_SPIKE_DATABASE_URL"
SPIKE_TABLE = "phase2c_vector_spike"
VECTOR_DIMENSIONS = 384
VECTOR_COUNT = 750
TOP_K = 10


def _synthetic_vector(candidate_id: uuid.UUID, content_hash: str) -> tuple[float, ...]:
    """Generate exactly 384 finite floats from stable, non-semantic input."""

    seed = f"{candidate_id}:{content_hash}".encode()
    values: list[float] = []
    counter = 0
    while len(values) < VECTOR_DIMENSIONS:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for offset in range(0, len(digest), 4):
            raw_value = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((raw_value / 2**32) * 2.0 - 1.0)
            if len(values) == VECTOR_DIMENSIONS:
                break
        counter += 1
    return tuple(values)


def _vector_literal(vector: Iterable[float]) -> str:
    """Serialize a deterministic vector as a PostgreSQL vector literal."""

    values = tuple(vector)
    assert len(values) == VECTOR_DIMENSIONS
    assert all(value == value and abs(value) != float("inf") for value in values)
    return "[" + ",".join(f"{value:.10f}" for value in values) + "]"


def _candidate_fixture(index: int) -> tuple[uuid.UUID, str, tuple[float, ...]]:
    candidate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"evidenceforge-phase2c-spike:{index}")
    content_hash = hashlib.sha256(
        f"evidenceforge-phase2c-spike-content:{index}".encode()
    ).hexdigest()
    return candidate_id, content_hash, _synthetic_vector(candidate_id, content_hash)


def _query_top_k(
    connection: psycopg.Connection,
    query_vector: str,
) -> tuple[tuple[tuple[str, float], ...], float]:
    with connection.cursor() as cursor:
        started = time.perf_counter()
        cursor.execute(
            f"""
            WITH nearest AS MATERIALIZED (
                SELECT
                    id::text AS id,
                    embedding <=> %s::vector AS cosine_distance
                FROM {SPIKE_TABLE}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            )
            SELECT id, cosine_distance
            FROM nearest
            ORDER BY cosine_distance, id
            """,
            (query_vector, query_vector, TOP_K),
        )
        rows = tuple((row[0], float(row[1])) for row in cursor.fetchall())
    return rows, (time.perf_counter() - started) * 1000.0


def _public_tables(connection: psycopg.Connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        return {row[0] for row in cursor.fetchall()}


def test_pgvector_spike_exact_and_hnsw_search() -> None:
    """Prove isolated vector insertion, exact search, HNSW, and comparison."""

    database_url = os.getenv(SPIKE_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"Set {SPIKE_DATABASE_URL_ENV} to run the disposable PostgreSQL/pgvector spike")

    with psycopg.connect(database_url) as connection:
        database_name = (connection.info.dbname or "").lower()
        if "spike" not in database_name:
            pytest.fail(
                "Refusing to run against a database without 'spike' in its name; "
                "use the isolated pgvector spike database"
            )

        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            server_version = cursor.fetchone()[0]
            assert server_version.startswith("16"), server_version

            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            extension_version = cursor.fetchone()[0]
            assert extension_version == "0.8.6", extension_version

            public_tables = _public_tables(connection)
            assert public_tables == set()

        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {SPIKE_TABLE}")
                cursor.execute(
                    f"""
                    CREATE TABLE {SPIKE_TABLE} (
                        id UUID PRIMARY KEY,
                        content_hash TEXT NOT NULL,
                        embedding vector({VECTOR_DIMENSIONS}) NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute AS a
                    JOIN pg_class AS c ON c.oid = a.attrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relname = %s
                      AND a.attname = 'embedding'
                    """,
                    (SPIKE_TABLE,),
                )
                assert cursor.fetchone()[0] == f"vector({VECTOR_DIMENSIONS})"

            fixtures = tuple(_candidate_fixture(index) for index in range(VECTOR_COUNT))
            target_id, _, target_vector = fixtures[VECTOR_COUNT // 2]
            query_literal = _vector_literal(target_vector)
            insert_rows = tuple(
                (str(candidate_id), content_hash, _vector_literal(vector))
                for candidate_id, content_hash, vector in fixtures
            )

            with connection.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {SPIKE_TABLE} (id, content_hash, embedding)
                    VALUES (%s, %s, %s::vector)
                    """,
                    insert_rows,
                )
                cursor.execute(f"SELECT count(*) FROM {SPIKE_TABLE}")
                assert cursor.fetchone()[0] == VECTOR_COUNT

            exact_rows, exact_latency_ms = _query_top_k(connection, query_literal)
            repeated_exact_rows, _ = _query_top_k(connection, query_literal)
            assert exact_rows == repeated_exact_rows
            exact_ids = tuple(row[0] for row in exact_rows)
            assert exact_ids[0] == str(target_id)
            assert exact_rows[0][1] == 0.0

            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE INDEX phase2c_vector_spike_hnsw
                    ON {SPIKE_TABLE}
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )
                cursor.execute("SET hnsw.ef_search = 100")
                cursor.execute("SET enable_seqscan = off")
                cursor.execute(
                    f"""
                    EXPLAIN (COSTS OFF)
                    WITH nearest AS MATERIALIZED (
                        SELECT
                            id::text AS id,
                            embedding <=> %s::vector AS cosine_distance
                        FROM {SPIKE_TABLE}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    )
                    SELECT id, cosine_distance
                    FROM nearest
                    ORDER BY cosine_distance, id
                    """,
                    (query_literal, query_literal, TOP_K),
                )
                hnsw_plan = "\n".join(row[0] for row in cursor.fetchall())
                assert "phase2c_vector_spike_hnsw" in hnsw_plan

            hnsw_rows, hnsw_latency_ms = _query_top_k(connection, query_literal)
            repeated_hnsw_rows, _ = _query_top_k(connection, query_literal)
            assert hnsw_rows == repeated_hnsw_rows
            hnsw_ids = tuple(row[0] for row in hnsw_rows)
            assert hnsw_ids[0] == str(target_id)

            overlap_count = len(set(exact_ids).intersection(hnsw_ids))
            overlap_rate = overlap_count / TOP_K
            assert overlap_count >= 1

            public_tables = _public_tables(connection)
            assert public_tables == {SPIKE_TABLE}

            print(f"PostgreSQL server_version_num: {server_version}")
            print(f"pgvector extension version: {extension_version}")
            print(f"Synthetic vector count: {VECTOR_COUNT}")
            print(f"Vector dimensions: {VECTOR_DIMENSIONS}")
            print(f"Exact top-{TOP_K} IDs: {exact_ids}")
            print(f"HNSW top-{TOP_K} IDs: {hnsw_ids}")
            print(f"Exact/HNSW overlap: {overlap_count}/{TOP_K} ({overlap_rate:.2%})")
            print(f"Exact latency (ms, single run): {exact_latency_ms:.3f}")
            print(f"HNSW latency (ms, single run): {hnsw_latency_ms:.3f}")
            print("Synthetic vectors are infrastructure-test vectors only.")
            print("They do not measure semantic retrieval quality.")
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {SPIKE_TABLE}")
            connection.commit()
