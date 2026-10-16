"""Persistent SQLite storage for the PDF knowledge graph."""

import sqlite3


class Store:
    """Store graph entities, relationships, and searchable page text."""

    def __init__(self, path: str):
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                document TEXT NOT NULL DEFAULT '',
                page INTEGER NOT NULL DEFAULT -1,
                UNIQUE (kind, key)
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY,
                src INTEGER NOT NULL,
                dst INTEGER NOT NULL,
                kind TEXT NOT NULL,
                document TEXT NOT NULL DEFAULT '',
                page INTEGER NOT NULL DEFAULT -1,
                detail TEXT NOT NULL DEFAULT '',
                -- One edge per endpoint/type/document/page; detail is replaceable OCR text.
                UNIQUE (src, dst, kind, document, page),
                FOREIGN KEY (src) REFERENCES nodes (id),
                FOREIGN KEY (dst) REFERENCES nodes (id)
            );
            CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges (dst, kind);

            CREATE TABLE IF NOT EXISTS pages (
                document TEXT NOT NULL,
                page INTEGER NOT NULL,
                aircraft_type TEXT NOT NULL,
                system_code TEXT NOT NULL,
                subsystem_code TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (document, page)
            );
            CREATE INDEX IF NOT EXISTS idx_pages_system
                ON pages (system_code, subsystem_code);

            -- FTS5 UNINDEXED columns are stored as text: queries must bind page as str.
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                document UNINDEXED,
                page UNINDEXED,
                body
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT INTO meta (key, value)
            VALUES ('schema_version', '1')
            ON CONFLICT (key) DO NOTHING;
            """
        )
        self._connection.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._connection.close()

    def add_node(
        self,
        kind: str,
        key: str,
        label: str = "",
        document: str = "",
        page: int = -1,
    ) -> int:
        """Insert or update a node and return its stable integer ID."""
        cursor = self._connection.execute(
            """
            INSERT INTO nodes (kind, key, label, document, page)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (kind, key) DO UPDATE SET
                label = CASE WHEN excluded.label <> '' THEN excluded.label ELSE nodes.label END,
                document = CASE WHEN excluded.document <> '' THEN excluded.document ELSE nodes.document END,
                page = CASE WHEN excluded.page >= 0 THEN excluded.page ELSE nodes.page END
            RETURNING id
            """,
            (kind, key, label, document, page),
        )
        node_id = cursor.fetchone()[0]
        self._connection.commit()
        return node_id

    def add_edge(
        self,
        src: int,
        dst: int,
        kind: str,
        detail: str = "",
        document: str = "",
        page: int = -1,
    ) -> None:
        """Insert an edge only if this exact relationship is not present."""
        self._connection.execute(
            """
            INSERT INTO edges (src, dst, kind, document, page, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (src, dst, kind, document, page)
            DO UPDATE SET detail = excluded.detail
            """,
            (src, dst, kind, document, page, detail),
        )
        self._connection.commit()

    def upsert_page(
        self,
        document: str,
        page: int,
        aircraft_type: str,
        system_code: str,
        subsystem_code: str,
        text: str,
        search_text: str,
    ) -> None:
        """Insert or replace page data and its corresponding FTS row.

        ``search_text`` must already be normalized by the caller (for example,
        with ``normalize_for_search``); this store does not normalize it.
        """
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO pages (
                    document, page, aircraft_type, system_code, subsystem_code, text
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (document, page) DO UPDATE SET
                    aircraft_type = excluded.aircraft_type,
                    system_code = excluded.system_code,
                    subsystem_code = excluded.subsystem_code,
                    text = excluded.text
                """,
                (document, page, aircraft_type, system_code, subsystem_code, text),
            )
            self._connection.execute(
                "DELETE FROM pages_fts WHERE document = ? AND page = ?",
                (document, str(page)),
            )
            self._connection.execute(
                "INSERT INTO pages_fts (document, page, body) VALUES (?, ?, ?)",
                (document, str(page), search_text),
            )

    def page_text(self, document: str, page: int) -> str:
        """Return page text, or an empty string when the page is absent."""
        row = self._connection.execute(
            "SELECT text FROM pages WHERE document = ? AND page = ?",
            (document, page),
        ).fetchone()
        return row[0] if row is not None else ""

    def stats(self) -> dict[str, int]:
        """Return counts of graph records and selected node types."""
        nodes = self._connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = self._connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        sheets = self._connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'sheet'"
        ).fetchone()[0]
        pending = self._connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'pending_reference'"
        ).fetchone()[0]
        return {"nodes": nodes, "edges": edges, "sheets": sheets, "pending": pending}
