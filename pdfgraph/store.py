"""Persistent SQLite storage for the PDF knowledge graph."""

import logging
import sqlite3
from datetime import datetime

#: Stage-2 diagnostics: the FTS SQL is logged at DEBUG (visible with
#: ``pdfgraph ask --debug``).
logger = logging.getLogger(__name__)

#: Current schema version. Additive changes (new ``CREATE TABLE IF NOT
#: EXISTS`` tables) are applied on every open; :class:`Store` bumps the
#: recorded version of older databases to this value (see __init__).
#: v4 (card 0.2, 29.10.2026): pages gains needs_rescan/is_service triplets.
SCHEMA_VERSION = 4


class Store:
    """Store graph entities, relationships, and searchable page text."""

    def __init__(self, path: str):
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            f"""
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

            -- Card 0.1b step 2: read-only-duplicate marks, fully additive.
            -- A row with status 'duplicate' hides that document's sheets
            -- from every search branch (see _dup_filter); 'canonical'
            -- records the surviving edition the duplicates point to.
            -- Nothing here is created from `documents` — that table is a
            -- later-stage concern and must not appear yet.
            CREATE TABLE IF NOT EXISTS document_aliases (
                name TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_document_aliases_status
                ON document_aliases (status);

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
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts_trigram USING fts5(
                document UNINDEXED,
                page UNINDEXED,
                body,
                tokenize='trigram'
            );

            -- Sheets that landed in the re-scan report (task 2.7). Blank
            -- divider sheets never appear in that report, so they are never
            -- listed here; callers add rows only through mark_defect().
            CREATE TABLE IF NOT EXISTS defects (
                document TEXT NOT NULL,
                page INTEGER NOT NULL,
                PRIMARY KEY (document, page)
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT INTO meta (key, value)
            VALUES ('schema_version', '{SCHEMA_VERSION}')
            ON CONFLICT (key) DO NOTHING;
            """
        )
        # Пометки листов 0.2 (миграция 3→4): аддитивные колонки на pages —
        # needs_rescan (повреждённый OCR) и is_service (служебная страница),
        # каждая с причиной и score; вручную и авто управляются методами ниже.
        page_columns = {row[1] for row in self._connection.execute(
            "PRAGMA table_info(pages)")}
        for column, decl in (
            ("needs_rescan", "INTEGER NOT NULL DEFAULT 0"),
            ("needs_rescan_reason", "TEXT NOT NULL DEFAULT ''"),
            ("needs_rescan_score", "REAL NOT NULL DEFAULT 0.0"),
            ("is_service", "INTEGER NOT NULL DEFAULT 0"),
            ("is_service_reason", "TEXT NOT NULL DEFAULT ''"),
            ("is_service_score", "REAL NOT NULL DEFAULT 0.0"),
        ):
            if column not in page_columns:
                self._connection.execute(
                    f"ALTER TABLE pages ADD COLUMN {column} {decl}")
        # Migrate on open: the additive tables above bring an old database
        # to the current shape, so its recorded version must follow —
        # otherwise meta would keep claiming a schema it no longer has.
        self._connection.execute(
            "UPDATE meta SET value = ? "
            "WHERE key = 'schema_version' AND CAST(value AS INTEGER) < ?",
            (str(SCHEMA_VERSION), SCHEMA_VERSION),
        )
        if self._connection.execute(
            "SELECT 1 FROM pages_fts_trigram LIMIT 1"
        ).fetchone() is None:
            self._connection.execute(
                "INSERT INTO pages_fts_trigram (document, page, body) "
                "SELECT document, page, body FROM pages_fts"
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
            self._connection.execute(
                "DELETE FROM pages_fts_trigram WHERE document = ? AND page = ?",
                (document, str(page)),
            )
            self._connection.execute(
                "INSERT INTO pages_fts_trigram (document, page, body) "
                "VALUES (?, ?, ?)",
                (document, str(page), search_text),
            )

    def page_text(self, document: str, page: int) -> str:
        """Return page text, or an empty string when the page is absent."""
        row = self._connection.execute(
            "SELECT text FROM pages WHERE document = ? AND page = ?",
            (document, page),
        ).fetchone()
        return row[0] if row is not None else ""

    def mark_defect(self, document: str, page: int) -> None:
        """Record that a sheet landed in the re-scan report (idempotent).

        Blank divider sheets are section boundaries, not scan defects, and
        never reach this method: the report the caller parses
        (``pdfprepare.quality.flag_pages``) already excludes them.
        """
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO defects (document, page) VALUES (?, ?)",
                (document, page),
            )

    def defect_sheets(self) -> list[tuple[str, int]]:
        """Sheets flagged for re-scan, ``(document, page)`` sorted."""
        return self._connection.execute(
            "SELECT document, page FROM defects ORDER BY document, page"
        ).fetchall()

    def _sheet_exists(self, document: str, page: int) -> bool:
        """Есть ли лист ``(document, page)`` в таблице pages."""
        return self._connection.execute(
            "SELECT 1 FROM pages WHERE document = ? AND page = ?",
            (document, page),
        ).fetchone() is not None

    def _flag_state(self, flag: str, document: str, page: int
                    ) -> tuple[int, float, str] | None:
        """Текущее состояние флага листа или None, если листа нет."""
        row = self._connection.execute(
            f"SELECT {flag}, {flag}_score, {flag}_reason "
            "FROM pages WHERE document = ? AND page = ?",
            (document, page),
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def _mark_flag(self, flag: str, document: str, page: int,
                   reason: str) -> bool:
        """Ручная пометка одного флага.

        True при первом включении или переходе auto → manual (ручная
        причина переписывает авто и защищает лист от перепометки);
        False, если ручная пометка уже стоит.
        """
        state = self._flag_state(flag, document, page)
        if state is None:
            raise LookupError(
                f"mark_{flag}: no such sheet: {document!r}#{page}")
        if state[0] == 1 and state[2].startswith("manual:"):
            return False
        with self._connection:
            self._connection.execute(
                f"UPDATE pages SET {flag} = 1, {flag}_reason = ?, "
                f"{flag}_score = 1.0 WHERE document = ? AND page = ?",
                (reason, document, page),
            )
        return True

    def _unmark_flag(self, flag: str, document: str, page: int,
                     text: str) -> bool:
        """Снять ручным решением один флаг; True только при включённом флаге.

        Причина переписывается на ``text`` («manual: снято»), чтобы авто
        не вернул метку при пересборке: set_*_auto не трогает manual:*.
        """
        state = self._flag_state(flag, document, page)
        if state is None:
            raise LookupError(
                f"unmark_{flag}: no such sheet: {document!r}#{page}")
        if state[0] == 0:
            return False
        with self._connection:
            self._connection.execute(
                f"UPDATE pages SET {flag} = 0, {flag}_reason = ?, "
                f"{flag}_score = 0.0 WHERE document = ? AND page = ?",
                (text, document, page),
            )
        return True

    def _set_auto_flag(self, flag: str, document: str, page: int,
                       score: float, reason: str) -> bool:
        """Авто-пометка при build: auto переписывается, manual — нет.

        Возвращает True, только если флаг был выключен или состояние было
        другое (auto → auto). Причина «manual: ...» неприкосновенна.
        """
        state = self._flag_state(flag, document, page)
        if state is None:
            raise LookupError(
                f"set_{flag}_auto: no such sheet: {document!r}#{page}")
        if state[2].startswith("manual:"):
            return False
        if state == (1, score, reason):
            return False
        with self._connection:
            self._connection.execute(
                f"UPDATE pages SET {flag} = 1, {flag}_reason = ?, "
                f"{flag}_score = ? WHERE document = ? AND page = ?",
                (reason, score, document, page),
            )
        return True

    def mark_rescan(self, document: str, page: int, reason: str) -> bool:
        """Пометить лист повреждённым (needs_rescan) вручную; см. _mark_flag."""
        return self._mark_flag("needs_rescan", document, page, reason)

    def unmark_rescan(self, document: str, page: int,
                      reason: str = "") -> bool:
        """Снять needs_rescan; reason дописывается в причину, см. _unmark_flag."""
        text = "manual: снято" + (f": {reason}" if reason else "")
        return self._unmark_flag("needs_rescan", document, page, text)

    def mark_service(self, document: str, page: int, reason: str) -> bool:
        """Пометить лист служебным (is_service) вручную; см. _mark_flag."""
        return self._mark_flag("is_service", document, page, reason)

    def unmark_service(self, document: str, page: int,
                       reason: str = "") -> bool:
        """Снять is_service; reason дописывается в причину, см. _unmark_flag."""
        text = "manual: снято" + (f": {reason}" if reason else "")
        return self._unmark_flag("is_service", document, page, text)

    def set_rescan_auto(self, document: str, page: int, score: float,
                        reason: str) -> bool:
        """Авто-пометка needs_rescan при build; см. _set_auto_flag."""
        return self._set_auto_flag("needs_rescan", document, page,
                                   score, reason)

    def set_service_auto(self, document: str, page: int, score: float,
                         reason: str) -> bool:
        """Авто-пометка is_service при build; см. _set_auto_flag."""
        return self._set_auto_flag("is_service", document, page,
                                   score, reason)

    def rescan_sheets(self) -> list[tuple[str, int]]:
        """Листы с needs_rescan, отсортированные (очередь перескана)."""
        return self._connection.execute(
            "SELECT document, page FROM pages WHERE needs_rescan = 1 "
            "ORDER BY document, page"
        ).fetchall()

    def service_sheets(self) -> list[tuple[str, int]]:
        """Листы с is_service (включая титулы), отсортированные."""
        return self._connection.execute(
            "SELECT document, page FROM pages WHERE is_service = 1 "
            "ORDER BY document, page"
        ).fetchall()

    def garbage_stats(self) -> dict[str, object]:
        """Счётчики помеченных: {total, rescan, service, reasons}.

        ``reasons`` — частоты причин обеих пометок (ручные и авто): то,
        что попадает в отчёт для владельца.
        """
        total = self._connection.execute(
            "SELECT COUNT(*) FROM pages").fetchone()[0]
        rescan = self._connection.execute(
            "SELECT COUNT(*) FROM pages WHERE needs_rescan = 1"
        ).fetchone()[0]
        service = self._connection.execute(
            "SELECT COUNT(*) FROM pages WHERE is_service = 1"
        ).fetchone()[0]
        reasons: dict[str, int] = {}
        for reason, count in self._connection.execute(
            "SELECT needs_rescan_reason, COUNT(*) FROM pages "
            "WHERE needs_rescan = 1 AND needs_rescan_reason <> '' "
            "GROUP BY needs_rescan_reason"
        ).fetchall():
            reasons[reason] = int(count)
        for reason, count in self._connection.execute(
            "SELECT is_service_reason, COUNT(*) FROM pages "
            "WHERE is_service = 1 AND is_service_reason <> '' "
            "GROUP BY is_service_reason"
        ).fetchall():
            reasons[reason] = reasons.get(reason, 0) + int(count)
        return {"total": total, "rescan": rescan, "service": service,
                "reasons": reasons}

    def distinct_documents(self, limit: int = 5) -> list[str]:
        """Most recent distinct ``pages.document`` keys, descending.

        Read-only helper for the builder window's post-build sanity check
        (plan task 3.1, fix П4): document keys must be bare file names
        (``"имя.pdf"``) — the same value :func:`pdfgraph.build._document_key`
        produces from the rewritten PDF inside the output directory.
        """
        rows = self._connection.execute(
            "SELECT DISTINCT document FROM pages ORDER BY document DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row[0] for row in rows]

    def find_node_id(self, kind: str, key: str) -> int | None:
        """Return the ID of the (kind, key) node, or None when it is absent.

        Read-only probe used to resolve references without creating a node.
        """
        row = self._connection.execute(
            "SELECT id FROM nodes WHERE kind = ? AND key = ?", (kind, key)
        ).fetchone()
        return row[0] if row is not None else None

    def has_node(self, kind: str, key: str) -> bool:
        """Return True when a node of this kind and key exists."""
        return self.find_node_id(kind, key) is not None

    def _dup_filter(self, document_ref: str = "pages.document") -> str:
        """SQL-условие, скрывающее листы документов-дублей (карточка 0.1б).

        ``document_ref`` — ссылка на колонку с именем документа в
        конкретном запросе (``pg.document`` для запросов через join).
        Таблица создаётся на каждом открытии (:meth:`__init__`), поэтому
        условие валидно и для старых баз: пустая таблица ничего не прячет.
        """
        return (
            " AND NOT EXISTS ("
            "SELECT 1 FROM document_aliases da "
            "WHERE da.name = " + document_ref +
            " AND da.status = 'duplicate')"
        )

    def mark_duplicate(
        self, name: str, canonical: str, reason: str = ""
    ) -> list[tuple[str, int]]:
        """Пометить ``name`` дублем ``canonical``; вернуть скрытые листы.

        Идемпотентно: повторный вызов переписывает канон/причину и не
        плодит строки. Листы и текст не удаляются — поиск лишь исключает
        документы со статусом ``duplicate`` из всех поисковых веток.
        Канон, если сам помечен дублем, отвергается — цепочка «дубль →
        дубль» не допускается.

        Raises:
            ValueError: ``name`` равен ``canonical`` или ``canonical``
                уже помечен дублем.
            LookupError: ``name`` или ``canonical`` отсутствует в таблице
                ``pages`` (у документа нет ни одного листа).
        """
        if name == canonical:
            raise ValueError(
                "mark_duplicate: name and canonical must differ: "
                f"{name!r}")
        if not self._document_in_pages(name):
            raise LookupError(f"mark_duplicate: no such document: {name!r}")
        if not self._document_in_pages(canonical):
            raise LookupError(
                f"mark_duplicate: no such canonical document: {canonical!r}")
        if self._alias_status(canonical) == "duplicate":
            raise ValueError(
                "mark_duplicate: canonical is itself marked duplicate: "
                f"{canonical!r}")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO document_aliases (
                    name, canonical_name, status, reason, created_at
                ) VALUES (?, ?, 'duplicate', ?, ?)
                ON CONFLICT (name) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    status = excluded.status,
                    reason = excluded.reason,
                    created_at = excluded.created_at
                """,
                (name, canonical, reason, self._now()),
            )
            self._connection.execute(
                """
                INSERT INTO document_aliases (
                    name, canonical_name, status, reason, created_at
                ) VALUES (?, ?, 'canonical', '', ?)
                ON CONFLICT (name) DO UPDATE SET
                    status = excluded.status,
                    created_at = excluded.created_at
                """,
                (canonical, canonical, self._now()),
            )
        return self.affected_sheets(name)

    def _document_in_pages(self, name: str) -> bool:
        """Есть ли хоть один лист документа ``name`` в таблице pages."""
        return self._connection.execute(
            "SELECT 1 FROM pages WHERE document = ? LIMIT 1", (name,)
        ).fetchone() is not None

    def _alias_status(self, name: str) -> str | None:
        """Текущий статус строки document_aliases для ``name`` или None."""
        row = self._connection.execute(
            "SELECT status FROM document_aliases WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row is not None else None

    def _now(self) -> str:
        """Локальная отметка времени для created_at (секунды)."""
        return datetime.now().isoformat(timespec="seconds")

    def unmark_duplicate(self, name: str) -> bool:
        """Снять пометку дубля у ``name``; вернуть True, если канон очищен.

        Идемпотентно: отсутствие помётки — нормальный no-op. Строка канона
        удаляется, когда на неё больше не указывает ни один дубль.
        """
        with self._connection:
            row = self._connection.execute(
                "SELECT canonical_name FROM document_aliases "
                "WHERE name = ? AND status = 'duplicate'",
                (name,),
            ).fetchone()
            canonical = row[0] if row is not None else ""
            self._connection.execute(
                "DELETE FROM document_aliases "
                "WHERE name = ? AND status = 'duplicate'",
                (name,),
            )
            if not canonical or self._connection.execute(
                "SELECT 1 FROM document_aliases "
                "WHERE name = ? AND status = 'canonical'",
                (canonical,),
            ).fetchone() is None:
                return False
            orphans = self._connection.execute(
                "SELECT COUNT(*) FROM document_aliases "
                "WHERE status = 'duplicate' AND canonical_name = ?",
                (canonical,),
            ).fetchone()[0]
            if orphans == 0:
                self._connection.execute(
                    "DELETE FROM document_aliases WHERE name = ?",
                    (canonical,))
                return True
        return False

    def list_aliases(self) -> list[tuple[str, str, str, str, str]]:
        """Все строки document_aliases: (name, status, canonical, reason, at)."""
        return self._connection.execute(
            "SELECT name, status, canonical_name, reason, created_at "
            "FROM document_aliases ORDER BY name"
        ).fetchall()

    def affected_sheets(self, name: str) -> list[tuple[str, int]]:
        """Листы документа, попадающие под пометку, отсортированные."""
        return self._connection.execute(
            "SELECT document, page FROM pages WHERE document = ? "
            "ORDER BY page",
            (name,),
        ).fetchall()

    def _garbage_filter(self, column_prefix: str = "") -> str:
        """SQL-условие, скрывающее помеченные листы (карточка 0.2).

        ``column_prefix``: "" для запросов прямо по таблице pages, "pg."
        для запросов с псевдонимом. needs_rescan скрывается всегда;
        is_service скрывается, кроме титулов каталога (причина начинается
        с «auto: титул») — титулы несут применимость и борта (0.3).
        Валидно и для старых баз: колонки создаются при открытии.
        """
        return (
            " AND " + column_prefix + "needs_rescan = 0 AND ("
            + column_prefix + "is_service = 0 OR "
            + column_prefix + "is_service_reason LIKE 'auto: титул%')"
        )

    def sheets_by_code(
        self,
        code: str,
        aircraft_type: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, int, str, str]]:
        """Sheets whose stored system_code or subsystem_code equals ``code``.

        Returns tuples ``(document, page, system_code, subsystem_code)``,
        ordered by document then page. ``aircraft_type`` (when given) keeps
        only sheets of that aircraft type; ``limit`` caps the row count.
        """
        sql = (
            "SELECT document, page, system_code, subsystem_code FROM pages "
            "WHERE (system_code = ? OR subsystem_code = ?)"
        )
        args: list[object] = [code, code]
        if aircraft_type is not None:
            sql += " AND aircraft_type = ?"
            args.append(aircraft_type)
        sql += self._dup_filter()
        sql += self._garbage_filter()
        sql += " ORDER BY document, page"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return self._connection.execute(sql, args).fetchall()

    def sheets_with_part(
        self,
        part_key: str,
        aircraft_type: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, int, str, str]]:
        """Sheets carrying a ``has_part`` edge to the part node with this key.

        Same tuple, ordering and filtering semantics as :meth:`sheets_by_code`;
        results are deduplicated per sheet and joined against ``pages`` for
        the code columns.
        """
        sql = (
            "SELECT pg.document, pg.page, pg.system_code, pg.subsystem_code "
            "FROM edges e "
            "JOIN nodes dst ON dst.id = e.dst "
            "AND dst.kind = 'part' AND dst.key = ? "
            "JOIN pages pg ON pg.document = e.document AND pg.page = e.page "
            "WHERE e.kind = 'has_part'"
        )
        args: list[object] = [part_key]
        if aircraft_type is not None:
            sql += " AND pg.aircraft_type = ?"
            args.append(aircraft_type)
        sql += self._dup_filter("pg.document")
        sql += self._garbage_filter("pg.")
        sql += " GROUP BY pg.document, pg.page ORDER BY pg.document, pg.page"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return self._connection.execute(sql, args).fetchall()

    def search_pages(
        self,
        match_expr: str,
        aircraft_type: str | None = None,
        limit: int | None = None,
        document_terms: tuple[str, ...] | None = None,
    ) -> list[tuple[str, int, str, str, float]]:
        """FTS5 search over the stored (already normalized) page bodies.

        ``match_expr`` is a ready FTS5 MATCH expression — building and
        escaping it is the caller's job. Returns tuples
        ``(document, page, system_code, subsystem_code, bm25)``, best rank
        first (bm25 is negative; smaller means better). The FTS table keeps
        page as a string, so rows are matched back to ``pages`` through
        ``CAST(pages_fts.page AS INTEGER)``.
        """
        sql = (
            "SELECT pg.document, pg.page, pg.system_code, pg.subsystem_code, "
            "bm25(pages_fts) AS rank "
            "FROM pages_fts "
            "JOIN pages pg ON pg.document = pages_fts.document "
            "AND pg.page = CAST(pages_fts.page AS INTEGER) "
            "WHERE pages_fts MATCH ?"
        )
        args: list[object] = [match_expr]
        if aircraft_type is not None:
            sql += " AND pg.aircraft_type = ?"
            args.append(aircraft_type)
        if document_terms:
            sql += " AND (" + " OR ".join(
                "pg.document LIKE ?" for _ in document_terms
            ) + ")"
            args.extend(f"%{term}%" for term in document_terms)
        sql += self._dup_filter("pg.document")
        sql += self._garbage_filter("pg.")
        sql += " ORDER BY rank"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        logger.debug("search_pages sql=%r args=%r", sql, args)
        return self._connection.execute(sql, args).fetchall()

    def search_trigrams(
        self,
        pattern: str,
        aircraft_type: str | None = None,
        document_terms: tuple[str, ...] | None = None,
    ) -> list[tuple[str, int, str, str]]:
        """Search the trigram-indexed normalized text with a LIKE pattern."""
        sql = (
            "SELECT pg.document, pg.page, pg.system_code, pg.subsystem_code "
            "FROM pages_fts_trigram "
            "JOIN pages pg ON pg.document = pages_fts_trigram.document "
            "AND pg.page = CAST(pages_fts_trigram.page AS INTEGER) "
            "WHERE pages_fts_trigram.body LIKE ?"
        )
        args: list[object] = [pattern]
        if aircraft_type is not None:
            sql += " AND pg.aircraft_type = ?"
            args.append(aircraft_type)
        if document_terms:
            sql += " AND (" + " OR ".join(
                "pg.document LIKE ?" for _ in document_terms
            ) + ")"
            args.extend(f"%{term}%" for term in document_terms)
        sql += self._dup_filter("pg.document")
        sql += self._garbage_filter("pg.")
        sql += " ORDER BY pg.document, pg.page"
        return self._connection.execute(sql, args).fetchall()

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
