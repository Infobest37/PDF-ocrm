"""Console interface for the PDF knowledge graph: ``python -m pdfgraph``.

Subcommands
-----------
``build <prepared_dir> <store_path> --type <ВС> [--registry <path>]``
    Reads every ``*.json`` under ``prepared_dir`` (recursively) with
    :func:`pdfprepare.output.read_result`, assembles the graph with
    :func:`pdfgraph.build.build` and prints :meth:`pdfgraph.store.Store.stats`.
    Then it parses the ``<stem>.report.txt`` re-scan report lying next to
    every JSON and marks those sheets with :meth:`Store.mark_defect`
    (idempotent; blank divider sheets are never in the report, hence never
    marked). Without ``--registry`` no registry is read at all
    (``registry = {}``); with ``--registry`` but a missing file, a warning
    goes to stderr and the registry stays empty.

``ask "<вопрос>" --store <path> [--type <ВС>] [--json]``
    Answers through :func:`pdfgraph.answer.build_answer` and **always** reports
    the :func:`pdfgraph.verify.verify_citations` result: one line per citation
    (document, sheet, system code) plus a verdict — how many citations were
    confirmed, or the refusal reason.

Exit codes (project contract: an answer is only trustworthy with verified
citations):
    0 — answered and every citation verified;
    1 — refusal, or at least one citation failed verification
        (in ``--json`` mode the same fact is the ``verified`` field);
    2 — usage/environment error (no JSON to build from, unreadable JSON,
        store file missing or not a database; a missing registry file is
        only a warning).
"""

import argparse
import dataclasses
import glob
import json
import os
import re
import sqlite3
import sys

from pdfgraph.answer import Answer, build_answer
from pdfgraph.build import _document_key, build
from pdfgraph.store import Store
from pdfgraph.verify import CitationCheck, all_verified, verify_citations
from pdfprepare.models import DocResult
from pdfprepare.output import read_result
from pdfprepare.registry import parse_registry

#: One re-scan report line written by ``pdfprepare.quality.write_report``.
_REPORT_LINE_RE = re.compile(r"^page (\d+): \S")


def _open_store(path: str) -> Store | None:
    """Open the SQLite store, reporting failures instead of crashing."""
    try:
        return Store(path)
    except sqlite3.Error as exc:
        print(f"error: cannot open store {path!r}: {exc}", file=sys.stderr)
        return None


def _load_registry(path: str | None) -> dict[str, str]:
    """Registry for ``build``: ``--registry`` absent or unreadable -> ``{}``.

    Without the flag no file is consulted at all (contract of task 2.6,
    review round 1). With the flag but a missing file: a warning on stderr
    and an empty registry, mirroring the warning style of
    :func:`pdfprepare.cli.main`.
    """
    if path is None:
        return {}
    if os.path.isfile(path):
        return parse_registry(path)
    print(
        f"warning: registry file not found: {path!r}; "
        "system labels come from page text only",
        file=sys.stderr,
    )
    return {}


def _mark_defects(store: Store,
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


def _cmd_build(args: argparse.Namespace) -> int:
    """Fill the store from prepared-document JSON; report stats."""
    paths = sorted(glob.glob(os.path.join(args.prepared_dir, "**", "*.json"),
                              recursive=True))
    if not paths:
        print(f"error: no *.json under {args.prepared_dir!r}", file=sys.stderr)
        return 2

    registry = _load_registry(args.registry)

    results = []
    for path in paths:
        try:
            result = read_result(path)
            # The JSON's position in the recursively searched prepared tree is
            # the stable identity; the source PDF may live elsewhere or share
            # its basename with another input. Its matching prepared PDF has
            # the same relative path with a .pdf suffix.
            relative_pdf = os.path.splitext(os.path.relpath(
                path, args.prepared_dir
            ))[0]
            if not relative_pdf.lower().endswith(".pdf"):
                relative_pdf += ".pdf"
            result = dataclasses.replace(
                result,
                source_path=os.path.join(args.prepared_dir, relative_pdf),
            )
            results.append(result)
        except (OSError, ValueError) as exc:  # JSONDecodeError ⊂ ValueError
            print(f"error: cannot read {path!r}: {exc}", file=sys.stderr)
            return 2

    store = _open_store(args.store_path)
    if store is None:
        return 2
    try:
        stats = build(results, args.aircraft_type, registry, store,
                      root=args.prepared_dir)
        defects = _mark_defects(store, list(zip(paths, results)),
                                root=args.prepared_dir)
    finally:
        store.close()

    print(
        f"nodes={stats['nodes']} edges={stats['edges']} "
        f"sheets={stats['sheets']} pending={stats['pending']} "
        f"defects={defects} "
        f"(документов: {len(results)})"
    )
    return 0


def _evidence_rows(answer: Answer, checks: list[CitationCheck]) -> list[dict]:
    """One row per citation: where it points, the sheet's code and the verdict."""
    by_sheet = {(fragment.document, fragment.page): fragment
                for fragment in answer.fragments}
    rows = []
    for check in checks:
        fragment = by_sheet.get((check.document, check.page))
        rows.append({
            "claim_index": check.claim_index,
            "document": check.document,
            "page": check.page,
            "system_code": fragment.system_code if fragment else "",
            "subsystem_code": fragment.subsystem_code if fragment else "",
            "ok": check.ok,
            "reason": check.reason,
        })
    return rows


def _print_json(answer: Answer, rows: list[dict], verified: bool) -> None:
    """Machine-readable answer: claims with citations, fragments, verdict."""
    claims = [
        {
            "text": claim.text,
            "evidence": list(claim.evidence),
            "citations": [row for row in rows if row["claim_index"] == index],
        }
        for index, claim in enumerate(answer.claims)
    ]
    payload = {
        "question": answer.question,
        "refusal": answer.refusal,
        "reason": answer.reason,
        "verified": verified,
        "claims": claims,
        "fragments": [dataclasses.asdict(fragment)
                      for fragment in answer.fragments],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_text(answer: Answer, rows: list[dict], verified: bool) -> None:
    """Human-readable answer: claims, «Доказательства» block, verdict."""
    print(f"Вопрос: {answer.question}")
    if answer.claims:
        print("Ответ:")
        for claim in answer.claims:
            print(f"  - {claim.text}")
    if rows:
        print("Доказательства:")
        for row in rows:
            sheet = f"{row['document'] or '?'} лист {row['page']}"
            code = row["system_code"] or row["subsystem_code"] or "без кода"
            mark = "ok" if row["ok"] else "НЕ ПОДТВЕРЖДЕНА"
            line = f"  [{mark}] {sheet} код {code}"
            if not row["ok"] and row["reason"]:
                line += f" — {row['reason']}"
            print(line)
    # Результат verify_citations печатается всегда: количество цитат и вердикт.
    ok = sum(1 for row in rows if row["ok"])
    print(f"Подтверждено {ok} из {len(rows)}")
    if answer.refusal:
        # Причина отказа печатается на stdout: она и есть ответ инструмента.
        print(f"Отказ: {answer.reason}")
    elif not verified:
        print("Вердикт: ответ неполный — есть неподтверждённые цитаты")
    else:
        print("Вердикт: все цитаты подтверждены")


def _cmd_ask(args: argparse.Namespace) -> int:
    """Answer a question and always report citation verification."""
    if not os.path.isfile(args.store_path):
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2

    store = _open_store(args.store_path)
    if store is None:
        return 2
    try:
        answer = build_answer(store, args.question, args.aircraft_type)
        checks = verify_citations(answer, store)
    finally:
        store.close()

    verified = all_verified(checks) and not answer.refusal
    rows = _evidence_rows(answer, checks)
    if args.as_json:
        _print_json(answer, rows, verified)
    else:
        _print_text(answer, rows, verified)
    return 0 if verified else 1


def main(argv: list[str]) -> int:
    """Dispatch ``pdfgraph`` subcommands; return the process exit code."""
    parser = argparse.ArgumentParser(
        prog="pdfgraph",
        description="Knowledge graph over prepared PDF documents: build the "
                    "store and answer questions with machine-verified "
                    "citations.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build_parser = commands.add_parser(
        "build", help="build the graph from prepared-document JSON.")
    build_parser.add_argument(
        "prepared_dir",
        help="Directory containing schema-v2 *.json results (searched "
             "recursively).")
    build_parser.add_argument(
        "store_path", help="SQLite graph file to create or update.")
    build_parser.add_argument(
        "--type", dest="aircraft_type", required=True, metavar="ВС",
        help="Aircraft type the documents belong to (e.g. Ми-8МТВ).")
    build_parser.add_argument(
        "--registry", default=None, metavar="PATH",
        help="ATA-100 code -> name registry file; without this flag no "
             "registry is read (labels come from the JSON system_names "
             "and page text).")
    build_parser.set_defaults(handler=_cmd_build)

    ask_parser = commands.add_parser(
        "ask", help="Answer a question; always print the citation verdict.")
    ask_parser.add_argument("question", help="Question in plain language.")
    ask_parser.add_argument(
        "--store", dest="store_path", required=True, metavar="PATH",
        help="SQLite graph file produced by 'build'.")
    ask_parser.add_argument(
        "--type", dest="aircraft_type", default=None, metavar="ВС",
        help="Restrict answers to sheets of this aircraft type.")
    ask_parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Print the answer as JSON (verified citations are the "
             "'verified' field).")
    ask_parser.set_defaults(handler=_cmd_ask)

    args = parser.parse_args(argv)
    return args.handler(args)
