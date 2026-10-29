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
import logging
import os
import pathlib
import sqlite3
import sys
from datetime import datetime

from pdfgraph.answer import Answer, build_answer
from pdfgraph.build import build, mark_defects
from pdfgraph.paths import resolve_store_path
from pdfgraph.store import Store
from pdfgraph.verify import CitationCheck, all_verified, verify_citations
from pdfprepare.output import read_result
from pdfprepare.registry import parse_registry


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
        defects = mark_defects(store, list(zip(paths, results)),
                               root=args.prepared_dir)
        flags = store.garbage_stats()
    finally:
        store.close()

    print(
        f"nodes={stats['nodes']} edges={stats['edges']} "
        f"sheets={stats['sheets']} pending={stats['pending']} "
        f"defects={defects} "
        f"rescan={flags['rescan']} service={flags['service']} "
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
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG, stream=sys.stderr,
            format="%(levelname)s %(name)s: %(message)s")
    try:
        store_path = resolve_store_path(args.store_path)
    except FileNotFoundError as exc:
        # Same contract as before (exit 2, «run 'pdfgraph build' first»);
        # Stage 2 adds the exact list of paths the resolver checked.
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    store = _open_store(store_path)
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


def _log_duplicate(
    store_path: str, action: str, name: str,
    canonical: str, reason: str, sheets: int,
) -> None:
    """Append one audit line to duplicates.log next to the graph file."""
    log_path = pathlib.Path(store_path).with_name("duplicates.log")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{stamp} {action} name={name} canonical={canonical} "
        f"reason={reason} sheets={sheets}\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _log_garbage(store_path: str, action: str, document: str,
                 page: int, reason: str, changed: bool) -> None:
    """Append one audit line to garbage.log next to the graph file (0.2)."""
    log_path = pathlib.Path(store_path).with_name("garbage.log")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{stamp} {action} document={document} page={page} "
        f"reason={reason} changed={int(changed)}\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _safe_print(text: str, stream=None) -> None:
    """Print text, surviving console encodings lacking some characters.

    Реальная Windows-консоль (cp1251/cp866) не содержит «→» и «…»:
    TextIOWrapper бросает UnicodeEncodeError при печати. Замена
    непредставимых знаков на «?» затрагивает только вывод консоли;
    данные базы и duplicates.log остаются нетронутыми.
    """
    stream = stream if stream is not None else sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write((text + "\n").encode(encoding, "replace")
                     .decode(encoding, "replace"))


def _cmd_mark_duplicate(args: argparse.Namespace) -> int:
    """Mark a document duplicate; its sheets leave every search branch."""
    store_path = pathlib.Path(resolve_store_path(args.store_path))
    if not store_path.exists():
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2
    if args.name == args.canonical:
        print("error: --name must differ from --canonical", file=sys.stderr)
        return 2
    store = Store(str(store_path))
    try:
        try:
            touched = store.mark_duplicate(
                args.name, args.canonical, reason=args.reason)
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _log_duplicate(str(store_path), "mark-duplicate", args.name,
                       args.canonical, args.reason, len(touched))
        if touched:
            listed = ", ".join(str(page) for _doc, page in touched)
            _safe_print(f"mark-duplicate: «{args.name}» → канон "
                        f"«{args.canonical}»; листов скрыто: {len(touched)} "
                        f"({listed})")
            for document, page in touched:
                text = store.page_text(document, page)
                quote = (text[:70] + "…") if len(text) > 70 else text
                _safe_print(f"  лист {page}: {quote}")
        else:
            _safe_print(f"mark-duplicate: «{args.name}» → канон "
                        f"«{args.canonical}»; повторная пометка, список "
                        "листов не изменился")
        _safe_print("Пометка записана в document_aliases (дубль исключён "
                    "из всех веток поиска; листы и текст не удалялись)")
    finally:
        store.close()
    return 0


def _cmd_unmark_duplicate(args: argparse.Namespace) -> int:
    """Remove the duplicate mark; sheets return to every search branch."""
    store_path = pathlib.Path(resolve_store_path(args.store_path))
    if not store_path.exists():
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2
    store = Store(str(store_path))
    try:
        orphan_marked = store.unmark_duplicate(args.name)
        if orphan_marked:
            _safe_print(f"unmark-duplicate: «{args.name}» пометка снята; "
                        "строка канона удалена из document_aliases (на "
                        "неё не указывал ни один дубль)")
        else:
            _safe_print(f"unmark-duplicate: «{args.name}» пометка снята "
                        "(или отсутствовала); строка канона не удалялась")
        _log_duplicate(str(store_path), "unmark-duplicate", args.name,
                       "", "", 0)
    finally:
        store.close()
    return 0


def _cmd_mark_flag(args: argparse.Namespace, flag: str) -> int:
    """Ручная пометка листа (mark-rescan / mark-service); idempotent.

    Лист уходит из поисковых веток (см. Store._garbage_filter), текст и
    узлы сохраняются; повторная пометка — норма (код 0, changed=0).
    Ошибка листа — код 2.
    """
    store_path = pathlib.Path(resolve_store_path(args.store_path))
    if not store_path.exists():
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2
    store = Store(str(store_path))
    try:
        try:
            changed = getattr(store, f"mark_{flag}")(
                args.document, args.page, args.reason)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _log_garbage(str(store_path), f"mark-{flag}", args.document,
                     args.page, args.reason, changed)
        text = store.page_text(args.document, args.page)
        quote = (text[:70] + "…") if len(text) > 70 else text
    finally:
        store.close()
    if changed:
        _safe_print(f"mark-{flag}: «{args.document}» лист {args.page} "
                    f"— {args.reason}")
        _safe_print(f"  лист {args.page}: {quote or '(без текста)'}")
        _safe_print(f"Пометка записана ({flag}); лист исключён из веток "
                    "поиска, цитаты остаются проверяемыми")
    else:
        _safe_print(f"mark-{flag}: «{args.document}» лист {args.page} "
                    "уже был помечен; причина сохранена прежней")
    return 0


def _cmd_unmark_flag(args: argparse.Namespace, flag: str) -> int:
    """Ручное снятие пометки листа (unmark-rescan / unmark-service)."""
    store_path = pathlib.Path(resolve_store_path(args.store_path))
    if not store_path.exists():
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2
    store = Store(str(store_path))
    try:
        try:
            changed = getattr(store, f"unmark_{flag}")(
                args.document, args.page, args.reason)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _log_garbage(str(store_path), f"unmark-{flag}", args.document,
                     args.page, args.reason, changed)
    finally:
        store.close()
    if changed:
        _safe_print(f"unmark-{flag}: «{args.document}» лист {args.page} "
                    "пометка снята; лист возвращается в поисковые ветки, "
                    "авто-разметка его не перепометит (manual-защита)")
    else:
        _safe_print(f"unmark-{flag}: «{args.document}» лист {args.page} "
                    "не был помечен; причина «manual: снято» не записана")
    return 0


def _cmd_mark_rescan(args: argparse.Namespace) -> int:
    """CLI-handler: mark-rescan (needs_rescan)."""
    return _cmd_mark_flag(args, "rescan")


def _cmd_unmark_rescan(args: argparse.Namespace) -> int:
    """CLI-handler: unmark-rescan (needs_rescan)."""
    return _cmd_unmark_flag(args, "rescan")


def _cmd_mark_service(args: argparse.Namespace) -> int:
    """CLI-handler: mark-service (is_service)."""
    return _cmd_mark_flag(args, "service")


def _cmd_unmark_service(args: argparse.Namespace) -> int:
    """CLI-handler: unmark-service (is_service)."""
    return _cmd_unmark_flag(args, "service")


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
    ask_parser.add_argument(
        "--debug", action="store_true",
        help="Log the question→branch→SQL→count chain to stderr.")
    ask_parser.set_defaults(handler=_cmd_ask)

    mark_parser = commands.add_parser(
        "mark-duplicate",
        help="Mark a document duplicate (idempotent, audited).")
    mark_parser.add_argument("store_path", help="SQLite graph file.")
    mark_parser.add_argument(
        "--name", required=True,
        help="Exact document name to mark duplicate.")
    mark_parser.add_argument(
        "--canonical", required=True,
        help="Exact document name of the surviving canonical edition.")
    mark_parser.add_argument(
        "--reason", default="",
        help="Why this duplicate was marked (stored and logged).")
    mark_parser.set_defaults(handler=_cmd_mark_duplicate)

    unmark_parser = commands.add_parser(
        "unmark-duplicate",
        help="Remove the duplicate mark (idempotent, audited).")
    unmark_parser.add_argument("store_path", help="SQLite graph file.")
    unmark_parser.add_argument(
        "--name", required=True,
        help="Exact document name whose duplicate mark is removed.")
    unmark_parser.set_defaults(handler=_cmd_unmark_duplicate)

    # Пометки листов 0.2 (пересмотр 29.10.2026): needs_rescan — исключён
    # из веток поиска до перескана; is_service — служебная страница.
    rescan_mark_parser = commands.add_parser(
        "mark-rescan",
        help="Mark a sheet as bad OCR, exclude from search branches.")
    rescan_mark_parser.add_argument("store_path", help="SQLite graph file.")
    rescan_mark_parser.add_argument(
        "--document", required=True, metavar="ИМЯ",
        help="Exact document name (as stored in pages).")
    rescan_mark_parser.add_argument(
        "--page", required=True, type=int, metavar="N",
        help="Sheet number to mark.")
    rescan_mark_parser.add_argument(
        "--reason", default="",
        help="Why the sheet is marked (stored and logged).")
    rescan_mark_parser.set_defaults(handler=_cmd_mark_rescan)

    rescan_unmark_parser = commands.add_parser(
        "unmark-rescan",
        help="Remove the needs_rescan mark (idempotent, audited).")
    rescan_unmark_parser.add_argument("store_path", help="SQLite graph file.")
    rescan_unmark_parser.add_argument(
        "--document", required=True, metavar="ИМЯ",
        help="Exact document name.")
    rescan_unmark_parser.add_argument(
        "--page", required=True, type=int, metavar="N",
        help="Sheet number to unmark.")
    rescan_unmark_parser.add_argument(
        "--reason", default="",
        help="Why the mark is removed (appended to the 'manual: снято' "
             "reason, protecting from auto re-marking).")
    rescan_unmark_parser.set_defaults(handler=_cmd_unmark_rescan)

    service_mark_parser = commands.add_parser(
        "mark-service",
        help="Mark a sheet service page (stamp/divider list, audited).")
    service_mark_parser.add_argument("store_path", help="SQLite graph file.")
    service_mark_parser.add_argument(
        "--document", required=True, metavar="ИМЯ",
        help="Exact document name (as stored in pages).")
    service_mark_parser.add_argument(
        "--page", required=True, type=int, metavar="N",
        help="Sheet number to mark.")
    service_mark_parser.add_argument(
        "--reason", default="",
        help="Why the sheet is marked (stored and logged).")
    service_mark_parser.set_defaults(handler=_cmd_mark_service)

    service_unmark_parser = commands.add_parser(
        "unmark-service",
        help="Remove the is_service mark (idempotent, audited).")
    service_unmark_parser.add_argument("store_path", help="SQLite graph file.")
    service_unmark_parser.add_argument(
        "--document", required=True, metavar="ИМЯ",
        help="Exact document name.")
    service_unmark_parser.add_argument(
        "--page", required=True, type=int, metavar="N",
        help="Sheet number to unmark.")
    service_unmark_parser.add_argument(
        "--reason", default="",
        help="Why the mark is removed (appended to the 'manual: снято' "
             "reason, protecting from auto re-marking).")
    service_unmark_parser.set_defaults(handler=_cmd_unmark_service)

    args = parser.parse_args(argv)
    return args.handler(args)
