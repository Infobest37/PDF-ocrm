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

``ask "<вопрос>" --store <path> [--type <ВС>] [--json] [--search-mode <режим>] [--context <id>]
    Answers through :func:`pdfgraph.answer.build_answer` and **always** reports
    the :func:`pdfgraph.verify.verify_citations` result: one line per citation
    (document, sheet or sheet range, system code) plus a verdict — how many
    citations were confirmed, or the refusal reason. A cited table
    continuation whose previous sheet is also cited prints as «листы 7–8».
    ``--search-mode`` переопределяет режим гибридного поиска
    (``lexical | hybrid_gated | hybrid_full``); по умолчанию берётся
    ``PDFGRAPH_SEARCH_MODE``, иначе ``hybrid_gated``.
    Ф1.4: размытый вопрос с непроверенной конкретикой даёт уточняющий
    вопрос вместо отказа (текст: «Требуется уточнение» и нумерованные
    варианты; ``--json``: поле ``clarify``) — код 3. ``--context <id>``
    включает память диалога (:mod:`pdfgraph.session`): реплика сливается
    с темой последнего уточнения, диалог хранится в ``dialog-<id>.json``
    рядом с графом (максимум три последние реплики); третье уточнение
    подряд превращается в детерминированный отказ о «лимите уточнений»
    (код 1). Без ``--context`` поведение прежнее одиночное.

``emb-index <store_path> [--chunk-size N] [--rebuild]``
    Ф1.0: пересчитывает флаги «продолжение таблицы» и строит постраничный
    чанк-векторный индекс (e5-small ONNX int8, офлайн; модель ставит
    ``scripts/fetch_models.ps1``, каталог — ``PDFGRAPH_MODELS_DIR``).
    Модель не найдена → печать ошибки и код 2. Идемпотентно: без
    ``--rebuild`` уже закодированные листы не пересчитываются.

Exit codes (project contract: an answer is only trustworthy with verified
citations):
    0 — answered and every citation verified;
    1 — refusal, or at least one citation failed verification
        (in ``--json`` mode the same fact is the ``verified`` field);
    2 — usage/environment error (no JSON to build from, unreadable JSON,
        store file missing or not a database, invalid ``--context`` id;
        a missing registry file is only a warning);
    3 — a clarifying question is issued instead of the answer (Ф1.4); in
        ``--json`` the same fact is the ``clarify`` field.
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
from pdfgraph.build import (
    auto_embeddings,
    build,
    mark_defects,
    mark_flags_auto,
)
from pdfgraph.paths import resolve_store_path
from pdfgraph.session import Session, load_context, save_context
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
        # Ф1.0: векторный индекс строится сразу после сборки, если модель
        # уже скачана; без модели поведение build прежнее (тихий пропуск).
        try:
            emb_report = auto_embeddings(store)
        except Exception as exc:  # noqa: BLE001 — сборка графа важнее векторов
            print(f"warning: auto-embeddings skipped: {exc}", file=sys.stderr)
            emb_report = None
    finally:
        store.close()

    print(
        f"nodes={stats['nodes']} edges={stats['edges']} "
        f"sheets={stats['sheets']} pending={stats['pending']} "
        f"defects={defects} "
        f"rescan={flags['rescan']} service={flags['service']} "
        f"(документов: {len(results)})"
    )
    if emb_report:
        _safe_print(
            f"emb: chunks={emb_report['chunks']} "
            f"embedded={emb_report['embedded']} "
            f"version={emb_report['version']} "
            f"continuation={emb_report['continuation']}")
    return 0


def _evidence_rows(answer: Answer, checks: list[CitationCheck],
                   store: Store | None = None) -> list[dict]:
    """One row per citation: where it points, the sheet's code and the verdict.

    ``store`` здесь не используется: строки остаются взаимно-однозначными с
    цитатами (счётчик «Подтверждено N из N» неизменен). Текстовый рендер
    принимает store отдельно, чтобы лист-продолжение печатался диапазоном
    «листы 7–8» (:func:`_print_text`); json-контракт не меняется.
    """
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
    # Ф1.4: уточняющий вопрос вместо отказа — поле clarify; при отказе
    # и при обычном ответе ключ не появляется.
    if answer.clarification and not answer.refusal:
        payload["clarify"] = {
            "question": str(answer.clarification.get("question", "")),
            "options": [dict(option) for option
                        in answer.clarification.get("options", [])],
        }
    # Ф1.7: окно соседних листов — отдельный список ссылок с подписью;
    # цитатами они не являются, в claims/citations не попадают.
    if getattr(answer, "neighbors", None) and not answer.refusal:
        payload["neighbors"] = [
            {"document": item["document"], "page": item["page"],
             "label": item["label"]}
            for item in answer.neighbors]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _continuation_sheet_label(store: Store, fragment, cited: set) -> str:
    """Метка листа цитаты; лист-продолжение таблицы → «листы 7–8».

    Диапазон показывается, только когда предыдущий лист тоже процитирован:
    иначе проверенный текст принадлежит одному листу, и указание чужого
    диапазона нарушило бы честность доказательства (Ф1.0).
    """
    document, page = fragment.document, fragment.page
    shown = document or "?"
    if (page > 1 and (document, page - 1) in cited
            and store.is_continuation(document, page)):
        return f"{shown} листы {page - 1}–{page}"
    return f"{shown} лист {page}"


def _print_text(answer: Answer, rows: list[dict], verified: bool,
                store: Store | None = None) -> None:
    """Human-readable answer: claims, «Доказательства» block, verdict.

    С ``store`` лист-продолжение, чей предыдущий лист тоже процитирован,
    печатается диапазоном «листы 7–8»; без ``store`` вывод прежний.
    """
    print(f"Вопрос: {answer.question}")
    if answer.clarification and not answer.refusal:
        # Ф1.4: уточняющий вопрос вместо отказа — нумерованные варианты.
        print("Требуется уточнение")
        for option in answer.clarification.get("options", []):
            print(f"  {option.get('id', '?')} — {option.get('label', '')}")
        return
    if answer.claims:
        print("Ответ:")
        for claim in answer.claims:
            print(f"  - {claim.text}")
    if rows:
        by_sheet = {(fragment.document, fragment.page): fragment
                    for fragment in answer.fragments}
        print("Доказательства:")
        for row in rows:
            fragment = by_sheet.get((row["document"], row["page"]))
            if store is not None and fragment is not None:
                sheet = _continuation_sheet_label(store, fragment, by_sheet)
            else:
                sheet = f"{row['document'] or '?'} лист {row['page']}"
            code = row["system_code"] or row["subsystem_code"] or "без кода"
            mark = "ok" if row["ok"] else "НЕ ПОДТВЕРЖДЕНА"
            line = f"  [{mark}] {sheet} код {code}"
            if not row["ok"] and row["reason"]:
                line += f" — {row['reason']}"
            print(line)
    # Ф1.7: окно соседних листов — ссылка и подпись, не цитаты; при отказе
    # ответа нет, блок не печатается.
    if getattr(answer, "neighbors", None) and not answer.refusal:
        print("Соседние листы (может быть продолжение):")
        for item in answer.neighbors:
            print(f"  {item['document'] or '?'} лист {item['page']}"
                  f" — {item['label']}")
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


def _record_session(session: Session, answer: Answer) -> None:
    """Дописать реплику диалога в сессию и сохранить файл (Ф1.4)."""
    if answer.refusal:
        session.record(answer.question, "refuse", reason=answer.reason)
    elif answer.clarification:
        session.record(
            answer.question, "clarify",
            reason=str(answer.clarification.get("reason", "")),
            options=list(answer.clarification.get("options", [])))
    else:
        session.record(answer.question, "answer")
    save_context(session)


def _cmd_ask(args: argparse.Namespace) -> int:
    """Ответить на вопрос и напечатать вердикт проверки цитат.

    Ф1.4: с ``--context`` работает память диалога — реплики ведутся в
    файле сессии рядом с графом; уточняющий вопрос (поле
    ``clarification`` ответа) завершает команду кодом 3, а третье
    уточнение подряд — детерминированный отказ без обращения к поиску.
    """
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

    session = None
    answer: Answer | None = None
    verified = False
    try:
        if getattr(args, "context", None):
            try:
                session = load_context(str(store_path), args.context)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            # Ф1.4: третье уточнение подряд — отказ без поиска; реплика
            # отказa дописывается в сессию здесь, общий блок ниже её
            # не дублирует.
            if session.clarify_limit_reached():
                merged = session.refine(args.question)
                answer = Answer(
                    question=merged,
                    claims=[],
                    fragments=[],
                    refusal=True,
                    reason="лимит уточнений: исчерпаны два уточнения "
                           "подряд — задайте вопрос целиком, ответ по "
                           "номеру варианта невозможен",
                )
                session.record(merged, "refuse", reason=answer.reason)
                save_context(session)
                verified = False
                if args.as_json:
                    _print_json(answer, [], False)
                else:
                    _print_text(answer, [], False)
                return 1  # finally закроет store
            question = session.refine(args.question)
        else:
            question = args.question
        answer = build_answer(store, question, args.aircraft_type,
                              search_mode=args.search_mode)
        checks = verify_citations(answer, store)
        # Печать идёт до закрытия store: текстовый рендер проверяет
        # store.is_continuation у листов-продолжений, процитированных
        # парой «листы 7–8» (Ф1.3б.1; печать после close падала с
        # sqlite3.ProgrammingError на закрытой БД).
        verified = all_verified(checks) and not answer.refusal
        rows = _evidence_rows(answer, checks, store)
        if args.as_json:
            _print_json(answer, rows, verified)
        else:
            _print_text(answer, rows, verified, store)
        if session is not None:
            _record_session(session, answer)
    finally:
        store.close()
    if (answer is not None and answer.clarification
            and not answer.refusal):
        return 3
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


def _log_documents(store_path: str, action: str, document: str,
                   detail: str) -> None:
    """Append one audit line to documents.log next to the graph (Ф0.3б)."""
    log_path = pathlib.Path(store_path).with_name("documents.log")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {action} document={document} {detail}\n")


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


def _cmd_docs_list(args: argparse.Namespace) -> int:
    """CLI-handler: docs-list (Ф0.3б) — таблица документов с полями."""
    from pdfgraph.documents import get_row

    store = Store(args.store_path)
    try:
        rows = store._connection.execute(
            "SELECT document, family, aircraft_type, layer, target, "
            "applicability_serials, applicability_scope, "
            "applicability_source, revision_no, revision_date, "
            "supersedes, review_status, notes FROM documents "
            "ORDER BY document").fetchall()
    finally:
        store.close()
    print(f"Документов: {len(rows)}")
    for row in rows:
        _safe_print(
            f"- {row[0]}\n"
            f"    family={row[1]} тип={row[2]} слой={row[3]} "
            f"цель={row[4] or '—'}\n"
            f"    применимость: scope={row[6]} борта=[{row[5] or '—'}] "
            f"источник={row[7]}\n"
            f"    ревизия: №{row[8] if row[8] is not None else '—'} "
            f"{row[9] or '—'}; supersedes={row[10] or '—'}\n"
            f"    статус={row[11]} заметки={row[12] or '—'}")
    return 0


def _cmd_docs_fill(args: argparse.Namespace) -> int:
    """CLI-handler: docs-fill (Ф0.3б) — идемпотентное заполнение."""
    from pdfgraph.documents import fill_missing

    store = Store(args.store_path)
    try:
        stats = fill_missing(store)
    finally:
        store.close()
    _log_documents(args.store_path, "fill", "*",
                   " ".join(f"{key}={value}"
                            for key, value in stats.items()))
    print(" ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


def _cmd_docs_set(args: argparse.Namespace) -> int:
    """CLI-handler: docs-set (Ф0.3б) — ручная правка, аудит в лог."""
    from pdfgraph.documents import set_fields

    store = Store(args.store_path)
    try:
        updated = set_fields(
            store, args.document, layer=args.layer, serials=args.serials,
            scope=args.scope, target=args.target, notes=args.notes)
    finally:
        store.close()
    detail = (f"layer={updated['layer']} serials="
              f"[{updated['applicability_serials']}] scope="
              f"{updated['applicability_scope']} source=manual")
    _log_documents(args.store_path, "set", args.document, detail)
    print(f"Обновлено: {args.document}; {detail}")
    return 0


def _cmd_docs_unset(args: argparse.Namespace) -> int:
    """CLI-handler: docs-unset (Ф0.3б) — откат к авто-значениям."""
    from pdfgraph.documents import unset_document

    store = Store(args.store_path)
    try:
        restored = unset_document(store, args.document)
    finally:
        store.close()
    _log_documents(args.store_path, "unset", args.document,
                   f"layer={restored['layer']} scope="
                   f"{restored['applicability_scope']} source="
                   f"{restored['applicability_source']}")
    print(f"Откат к авто-значениям: {args.document}")
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


def _log_flags_run(store_path: str, rescan: int, service: int,
                   manual: int, changed: int) -> None:
    """Append one mark-flags-auto audit line to garbage.log (0.2а)."""
    log_path = pathlib.Path(store_path).with_name("garbage.log")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (f"{stamp} mark-flags-auto rescan={rescan} service={service} "
            f"manual={manual} changed={changed}\n")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _cmd_mark_flags_auto(args: argparse.Namespace) -> int:
    """Разовая авто-разметка готовой базы правилами 0.2 без пересборки.

    Те же правила, что при build (:func:`pdfgraph.build.mark_flags_auto`
    переиспользует ``_mark_page_flags``). Идемпотентно: авто-метки
    перезаписываются auto → auto, причины «manual: ...» не перезаписываются.
    Печатает счётчики (rescan/service — все авто и ручные вместе, manual —
    листы, помеченные вручную) и число изменённых листов; одна
    аудит-строка пишется в garbage.log рядом с базой.
    """
    store_path = pathlib.Path(resolve_store_path(args.store_path))
    if not store_path.exists():
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2
    store = _open_store(str(store_path))
    if store is None:
        return 2
    try:
        changed = mark_flags_auto(store)
        flags = store.garbage_stats()
        manual = store._connection.execute(
            "SELECT COUNT(*) FROM pages WHERE "
            "(needs_rescan = 1 AND needs_rescan_reason LIKE 'manual:%') "
            "OR (is_service = 1 AND is_service_reason LIKE 'manual:%')"
        ).fetchone()[0]
    finally:
        store.close()
    _safe_print(
        f"mark-flags-auto: rescan={flags['rescan']} "
        f"service={flags['service']} manual={manual} changed={changed} "
        "(идемпотентно; причины «manual: …» не перезаписывались)")
    _log_flags_run(str(store_path), int(flags["rescan"]),
                   int(flags["service"]), int(manual), changed)
    return 0


def _cmd_emb_index(args: argparse.Namespace) -> int:
    """Ф1.0: пересчитать флаги продолжений и построить индекс.

    Модель не скачана (``FileNotFoundError``) или не установлен onnxruntime
    (``ImportError``) — печать ошибки и код 2. Идемпотентно: уже закодированные
    листы не пересчитываются без ``--rebuild``; флаги «продолжение таблицы»
    пересчитываются всегда. Печатает счётчики чанков,
    закодированных листов, версию индекса и число листов с продолжением.
    """
    from pdfgraph import embedding

    store_path = pathlib.Path(resolve_store_path(args.store_path))
    if not store_path.exists():
        print(f"error: store not found: {args.store_path!r} "
              "(run 'pdfgraph build' first)", file=sys.stderr)
        return 2
    try:
        embedding.model_file_paths(None)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    store = _open_store(str(store_path))
    if store is None:
        return 2
    try:
        if args.chunk_size is not None and args.chunk_size <= 0:
            print("error: --chunk-size must be a positive integer",
                  file=sys.stderr)
            return 2
        embedding.write_continuation_flags(store)
        kwargs = {}
        if args.chunk_size is not None:
            kwargs["chunk_size"] = args.chunk_size
        report = embedding.build_page_embeddings(
            store, rebuild=args.rebuild, **kwargs)
        continuations = sum(
            1 for document, page in store.searchable_pages()
            if store.is_continuation(document, page))
    except (FileNotFoundError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    _safe_print(
        f"emb-index: chunks={report['chunks']} "
        f"embedded={report['embedded']} version={report['version']} "
        f"continuation={continuations}")
    return 0


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
    ask_parser.add_argument(
        "--search-mode", dest="search_mode", default=None,
        choices=("lexical", "hybrid_gated", "hybrid_full"),
        help="Override the hybrid search mode for this run "
             "(default: PDFGRAPH_SEARCH_MODE, else hybrid_gated).")
    ask_parser.add_argument(
        "--context", default=None, metavar="ID",
        help="Dialog id (1..64 chars [A-Za-z0-9_-], Ф1.4): the reply is "
             "merged with the topic of the last clarification and the "
             "dialogue is kept in dialog-ID.json next to the store; "
             "without the flag the run is single-shot.")
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

    # Разовая авто-разметка готовой базы (0.2а, шаг приёмки 29.10): правила
    # те же, что при build; manual:* неприкосновенны; аудит в garbage.log.
    flags_auto_parser = commands.add_parser(
        "mark-flags-auto",
        help="Re-run 0.2 auto flagging over an existing store (idempotent).")
    flags_auto_parser.add_argument("store_path", help="SQLite graph file.")
    flags_auto_parser.set_defaults(handler=_cmd_mark_flags_auto)

    # Ф0.3б: слои и применимость документов (таблица documents, v6).
    docs_list_parser = commands.add_parser(
        "docs-list",
        help="List documents with layer/applicability fields (Ф0.3б).")
    docs_list_parser.add_argument("store_path", help="SQLite graph file.")
    docs_list_parser.set_defaults(handler=_cmd_docs_list)

    docs_fill_parser = commands.add_parser(
        "docs-fill",
        help="Idempotently fill missing rows of documents (Ф0.3б).")
    docs_fill_parser.add_argument("store_path", help="SQLite graph file.")
    docs_fill_parser.set_defaults(handler=_cmd_docs_fill)

    docs_set_parser = commands.add_parser(
        "docs-set",
        help="Manual edit of a documents row (audited, Ф0.3б).")
    docs_set_parser.add_argument("store_path", help="SQLite graph file.")
    docs_set_parser.add_argument(
        "--document", required=True, metavar="ИМЯ",
        help="Exact document name (as stored in pages).")
    docs_set_parser.add_argument(
        "--layer", choices=("base", "supplement", "change", "unknown"),
        default=None, help="Document layer.")
    docs_set_parser.add_argument(
        "--serials", default=None, metavar="СПИСОК",
        help="Applicable serials, comma-separated ('' = all).")
    docs_set_parser.add_argument(
        "--scope", choices=("all", "serials", "equipment"), default=None,
        help="Applicability scope.")
    docs_set_parser.add_argument(
        "--target", default=None, metavar="ЦЕЛЬ",
        help="What this supplement targets (РТЭ/РО/комплект).")
    docs_set_parser.add_argument(
        "--notes", default=None, help="Free-form note (audited).")
    docs_set_parser.set_defaults(handler=_cmd_docs_set)

    docs_unset_parser = commands.add_parser(
        "docs-unset",
        help="Revert a documents row to auto-computed values (Ф0.3б).")
    docs_unset_parser.add_argument("store_path", help="SQLite graph file.")
    docs_unset_parser.add_argument(
        "--document", required=True, metavar="ИМЯ",
        help="Exact document name.")
    docs_unset_parser.set_defaults(handler=_cmd_docs_unset)

    # Ф1.0: эмбеддинг-индекс готовой базы (офлайн, после
    # scripts\fetch_models.ps1).
    emb_parser = commands.add_parser(
        "emb-index",
        help="Build/rebuild the per-page chunk embedding index.")
    emb_parser.add_argument("store_path", help="SQLite graph file.")
    emb_parser.add_argument(
        "--chunk-size", dest="chunk_size", type=int, default=None,
        metavar="N",
        help="Chunk size in characters (default: 600).")
    emb_parser.add_argument(
        "--rebuild", action="store_true",
        help="Re-encode every sheet, ignoring the previous index.")
    emb_parser.set_defaults(handler=_cmd_emb_index)

    args = parser.parse_args(argv)
    return args.handler(args)
