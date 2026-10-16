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

from pdfprepare.codes import normalize_code, system_of
from pdfprepare.models import DocResult
from pdfprepare.normalize import normalize_for_search, normalize_part_number
from pdfgraph.store import Store

#: Longest page-text-derived label kept for codes missing from the registry.
_MAX_LABEL_CHARS = 120

#: Shortest normalized cell key accepted as a part designation (fix round 1:
#: bare ordinals/quantities like ``1.`` or ``3`` are table noise, not parts).
_PART_MIN_KEY_LEN = 6

#: At least one Cyrillic or Latin letter; keys are upper-cased by the
#: normalizer, both cases are listed for robustness.
_PART_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")


def _is_part_designation(key: str) -> bool:
    """Whether a normalized table cell qualifies as a part designation.

    Agreed rule (fix round 1): the ``normalize_part_number`` key must contain
    at least one Cyrillic or Latin letter, at least one dot, and be at least
    6 characters long. Everything else — empty cells, pure digits, ordinals,
    quantities, letterless codes — is skipped silently; nothing is guessed.
    """
    return (
        len(key) >= _PART_MIN_KEY_LEN
        and "." in key
        and _PART_LETTER_RE.search(key) is not None
    )


def _document_key(source_path: str) -> str:
    """Stable per-document key: the file name, independent of the folder it
    was prepared in (keeps the graph identical across machines)."""
    return os.path.basename(source_path)


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
) -> dict[str, int]:
    """Fill ``store`` with the graph described by ``results``; return stats.

    Creates the aircraft-type node, the systems/subsystems actually met in the
    pages (labelled from ``registry``, else ``DocResult.system_names``, else a
    page-text line for unnamed third-level codes), one document node, one sheet
    node per page, part nodes from table cells that pass the designation
    filter (:func:`_is_part_designation`), figure nodes, plus edges.
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
        doc_key = _document_key(result.source_path)
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
                for row in table.rows:
                    for cell in row:
                        part_key = normalize_part_number(cell)
                        if not _is_part_designation(part_key):
                            continue  # empty or non-designation cell, skipped
                        part_id = store.add_node("part", part_key)
                        store.add_edge(sheet_id, part_id, "has_part",
                                       detail=cell,
                                       document=doc_key, page=page.page_index)

    # Pass 2 — references (all structural nodes exist by now).
    for result in results:
        doc_key = _document_key(result.source_path)
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
