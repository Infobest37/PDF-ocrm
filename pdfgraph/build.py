"""Assemble the knowledge graph from prepared-document results.

``build()`` turns :class:`pdfprepare.models.DocResult` objects (JSON schema v2)
into nodes/edges/pages in a :class:`pdfgraph.store.Store`. It is idempotent:
every node is keyed by a stable string and every edge by its endpoints, kind,
document and page, so re-running a build never duplicates records.

Edge directions (fixed project contract):
``contained_in`` points child → parent (sheet → subsystem, subsystem → system,
system → type); ``documented_on`` points document → sheet; ``has_part`` and
``has_figure`` point sheet → part/figure; ``references_system`` and
``references_figure`` point sheet → the resolved (or pending) target.

Raw text is never rewritten: normalizations only produce lookup *keys*, while
evidence strings (raw cell, raw reference clause, caption) travel on edges.
"""

import os
import re
import sys

from pdfprepare.codes import normalize_code, system_of
from pdfprepare.models import DocResult
from pdfprepare.normalize import normalize_for_search, normalize_part_number
from pdfgraph.store import Store

#: Longest page-text-derived label kept for codes missing from the registry.
_MAX_LABEL_CHARS = 120

#: Shortest normalized cell key accepted as a part designation.
_PART_MIN_KEY_LEN = 6

#: Designation shape (fix round 3 added ``Ё``: normalize_part_number's
#: ``.upper()`` keeps ``Ё`` as U+0401, which lies outside the plain ``А-Я``
#: range). Starts alphanumeric, then only digits, uppercase letters (incl. Ё)
#: and ``.`` / ``-``. No spaces, brackets, ``®``, pipes — OCR noise like
#: ``КОЛИЧ. НА ЕД. СБ.`` fails on the very first character class.
_PART_SHAPE_RE = re.compile(r"^[0-9A-ZА-ЯЁ][0-9A-ZА-ЯЁ.\-]*$")

#: At least one Cyrillic or Latin letter (keys are upper-cased by the
#: normalizer, so uppercase suffices; Ё counted explicitly).
_PART_LETTER_RE = re.compile(r"[A-ZА-ЯЁ]")

#: A run of five or more leading digits — the alternative to having a letter
#: (keeps pure-numeric designations like ``869309.001-03``).
_PART_LEADING_DIGITS_RE = re.compile(r"^[0-9]{5}")


def _looks_like_part_number(key: str) -> bool:
    """Whether a normalized table-cell key is a part designation.

    Rule agreed with the coordinator (fix round 2, justified by real Каталог
    Д-1701 data; round 3 widens the classes with ``Ё``): the key must match
    ``^[0-9A-ZА-ЯЁ][0-9A-ZА-ЯЁ.\\-]*$``
    (no spaces, no punctuation beyond dot and hyphen, no ®), contain at least
    one dot, be at least 6 characters long, and contain at least one letter
    OR start with five or more digits (pure-numeric designations are kept).
    Drops ``1.``, ``3``, ``КОЛИЧ. НА ЕД. СБ.``, ``960-2. ® A OOO ®``;
    keeps ``8МТ.6101.100``, ``869309.001-03``. Keys arrive already upper-cased
    by :func:`pdfprepare.normalize.normalize_part_number`.
    """
    return (
        len(key) >= _PART_MIN_KEY_LEN
        and _PART_SHAPE_RE.match(key) is not None
        and "." in key
        and (_PART_LETTER_RE.search(key) is not None
             or _PART_LEADING_DIGITS_RE.match(key) is not None)
    )


def _document_key(source_path: str, root: str | None = None) -> str:
    """Document key relative to ``root``, or basename for legacy callers.

    Relative POSIX paths retain identity when different subfolders contain
    same-named PDFs while remaining portable across Windows and POSIX.
    """
    if root is None:
        return os.path.basename(source_path)
    return os.path.relpath(
        os.path.abspath(source_path), os.path.abspath(root)
    ).replace(os.sep, "/")


def _label_from_page_text(text: str, code: str) -> str:
    """First line of ``text`` containing ``code``, clipped to 120 characters.

    Used only for third-level codes that the registry does not name: the page
    heading is then the best available label. ``""`` when no line contains it.
    """
    for line in text.splitlines():
        if code in line:
            return line[:_MAX_LABEL_CHARS]
    return ""


def _system_label(code: str, registry: dict[str, str], names: dict[str, str],
                  page_text: str) -> str:
    """Best label for a system/subsystem node at ``code``."""
    label = registry.get(code, "") or names.get(code, "")
    if label:
        return label
    if code[3:] != ".00.00":  # third-level code absent from the registry
        return _label_from_page_text(page_text, code)
    return ""


def build(
    results: list[DocResult],
    aircraft_type: str,
    registry: dict[str, str],
    store: Store,
    root: str | None = None,
) -> dict[str, int]:
    """Fill ``store`` with the graph described by ``results``; return stats.

    When ``root`` is supplied, document keys are POSIX-normalized paths relative
    to it; otherwise direct callers retain the legacy basename key.

    Creates the aircraft-type node, the systems/subsystems actually met in the
    pages (labelled from ``registry``, else ``DocResult.system_names``, else a
    page-text line for unnamed third-level codes), one document node, one sheet
    node per page, part nodes from table cells that pass the designation
    pattern (:func:`_looks_like_part_number`), figure nodes, plus edges.
    Cross-references are resolved in a second pass, so a forward reference to a
    figure that appears later in the same document still resolves; missing
    targets become ``pending_reference`` nodes keyed ``system:<код>`` /
    ``figure:<документ>:<номер>``. A blank page is an ordinary sheet node — a
    section boundary, not a defect.

    Returns the same shape as :meth:`Store.stats`.
    """
    type_id = store.add_node("type", aircraft_type, label=aircraft_type)

    # Pass 1 — structural nodes/edges; remember sheets for reference linking.
    sheets: dict[tuple[str, int], int] = {}
    for result in results:
        doc_key = _document_key(result.source_path, root)
        doc_id = store.add_node("document", doc_key, label=doc_key)
        for page in result.pages:
            system_code = normalize_code(page.system_code)
            subsystem_code = normalize_code(page.subsystem_code)
            if subsystem_code and not system_code:
                system_code = system_of(subsystem_code)

            system_id = None
            if system_code:
                system_id = store.add_node(
                    "system", system_code,
                    label=_system_label(system_code, registry, result.system_names,
                                       page.full_text),
                )
                store.add_edge(system_id, type_id, "contained_in")

            subsystem_id = None
            if subsystem_code and subsystem_code != system_code:
                subsystem_id = store.add_node(
                    "subsystem", subsystem_code,
                    label=_system_label(subsystem_code, registry, result.system_names,
                                       page.full_text),
                )
                parent = system_id if system_id is not None else store.add_node(
                    "system", system_of(subsystem_code),
                    label=_system_label(system_of(subsystem_code), registry,
                                        result.system_names, page.full_text),
                )
                store.add_edge(subsystem_id, parent, "contained_in")

            sheet_id = store.add_node(
                "sheet", f"{doc_key}:{page.page_index}",
                document=doc_key, page=page.page_index,
            )
            sheets[(doc_key, page.page_index)] = sheet_id
            store.add_edge(doc_id, sheet_id, "documented_on",
                           document=doc_key, page=page.page_index)
            container = subsystem_id if subsystem_id is not None else system_id
            if container is not None:
                store.add_edge(sheet_id, container, "contained_in",
                               document=doc_key, page=page.page_index)

            store.upsert_page(
                doc_key, page.page_index, aircraft_type,
                system_code, subsystem_code,
                page.full_text, normalize_for_search(page.full_text),
            )

            for figure in page.figures:
                figure_id = store.add_node(
                    "figure", f"{doc_key}:{figure.number}",
                    label=figure.caption, document=doc_key, page=page.page_index,
                )
                store.add_edge(sheet_id, figure_id, "has_figure",
                               detail=figure.caption,
                               document=doc_key, page=page.page_index)

            for table in page.tables:
                for row_index, row in enumerate(table.rows):
                    for cell_index, cell in enumerate(row):
                        if (row_index < len(table.ambiguous)
                                and cell_index < len(table.ambiguous[row_index])
                                and table.ambiguous[row_index][cell_index]):
                            continue
                        part_key = normalize_part_number(cell)
                        if not _looks_like_part_number(part_key):
                            continue  # not a designation — silently skipped
                        part_id = store.add_node("part", part_key)
                        store.add_edge(sheet_id, part_id, "has_part",
                                       detail=cell,
                                       document=doc_key, page=page.page_index)

    # Pass 2 — references (all structural nodes exist by now).
    for result in results:
        doc_key = _document_key(result.source_path, root)
        for page in result.pages:
            sheet_id = sheets[(doc_key, page.page_index)]
            for ref in page.references:
                if ref.kind == "system":
                    target = normalize_code(ref.target)
                    if not target:
                        continue
                    dst = store.find_node_id("subsystem", target)
                    if dst is None:
                        dst = store.find_node_id("system", target)
                    if dst is None:
                        dst = store.add_node(
                            "pending_reference", f"system:{target}", label=target,
                        )
                    store.add_edge(sheet_id, dst, "references_system",
                                   detail=ref.raw,
                                   document=doc_key, page=page.page_index)
                elif ref.kind == "figure":
                    number = ref.target.strip()
                    if not number:
                        continue
                    dst = store.find_node_id("figure", f"{doc_key}:{number}")
                    if dst is None:
                        dst = store.add_node(
                            "pending_reference", f"figure:{doc_key}:{number}",
                            label=f"рис. {number}",
                            document=doc_key, page=page.page_index,
                        )
                    store.add_edge(sheet_id, dst, "references_figure",
                                   detail=ref.raw,
                                   document=doc_key, page=page.page_index)

    return store.stats()


#: One re-scan report line written by ``pdfprepare.quality.write_report``.
_REPORT_LINE_RE = re.compile(r"^page (\d+): \S")


def mark_defects(store: Store,
                 entries: list[tuple[str, DocResult]],
                 root: str | None = None) -> int:
    """Mark re-scan sheets from the pipeline reports that lie next to JSONs.

    ``entries`` pairs ``(json_path, DocResult)``. For every ``<stem>.json``
    the pipeline left ``<stem>.report.txt`` beside it (lines
    ``page N: low_confidence (...)`` from
    :func:`pdfprepare.quality.write_report`); every such page is recorded
    with the idempotent :meth:`Store.mark_defect` under the same document
    key the graph uses (:func:`pdfgraph.build._document_key`). Blank
    divider sheets never appear in the report, so they are never marked.
    A report line that does not parse is ignored. Returns the number of
    marked sheets.

    Public since the builder-window worker (task 3.1, fix П5) — the CLI and
    the GUI share this exact loop so defect marking cannot drift apart.
    """
    for path, result in entries:
        report = os.path.splitext(path)[0] + ".report.txt"
        if not os.path.isfile(report):
            continue
        try:
            with open(report, encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError) as exc:  # corrupt UTF-8 must not kill build
            print(f"warning: cannot read report {report!r}: {exc}",
                  file=sys.stderr)
            continue
        document = _document_key(result.source_path, root)
        for line in lines:
            match = _REPORT_LINE_RE.match(line.strip())
            if match:
                store.mark_defect(document, int(match.group(1)))
    return len(store.defect_sheets())
