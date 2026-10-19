# Product

<!-- impeccable:product-schema 1 -->

<!-- impeccable: inferred-only from the explicit design brief and repository evidence;
     this run is unattended (subagent without an answer mechanism). Every unconfirmed
     fact below is labelled (inferred). -->

## Platform

native

Windows desktop application (PyQt5 5.15.11, portable folder, no installation). The skill's
web/ios/android/adaptive enum has no desktop value; `native` is recorded as the truthful
closest class and web-only tooling (live mode, HTML/CSS detector) does not apply.

## Users

Aircraft maintenance/engineering documentation specialist working with scanned Mi-8MTV
helicopter documents (РТО, tech cards). Situation: at a workstation, offline, answering
"what does the document say about system X / part Y" while servicing or planning repairs.
Job: trust the answer or reject it, then go read the exact source sheet. (inferred from the
knowledge-graph plan and repo docs; the brief confirms the single-window task tool shape.)

## Product Purpose

A single-window question tool over the OCR knowledge graph: the user asks in plain Russian,
the app shows claims with their source document, sheet number and ATA-100 system code, and —
this is the core mechanism — every citation is machine-verified against verbatim stored page
text before being shown. Success = the engineer can tell, in seconds, whether the answer is
confirmed on sheets ("Подтверждено N из N") or honestly refused ("Ответ отклонён — нет
подтверждений на листах"). The tool must never pass unverified text off as knowledge.

## Positioning

Answers are extracts whose citations are re-checked against the original scanned text
(`verify_citations`), with a visible per-row verification state and a calm, explicit refusal
when nothing verifies. The verification verdict is a first-class part of the UI, not a
footnote; an honest "no evidence" is a correct result, not an error.

## Operating Context

Portable one-folder Windows app; data is a SQLite graph built from OCR'd scans of 49 Mi-8MTV
PDFs; navigation is by system/subsystem codes (XX.00.00 / XX.YY.ZZ), present in every page
header, not by sheet numbers. A blank page means a section divider, not a scan defect. The
usage scene is a bright workshop/office during task work, one window at a time. (domain facts
from AGENTS.md; light-scene inference from the brief's "высокий контраст, крупный шрифт".)

## Capabilities and Constraints

- Brief-pinned (binding): one window, nothing extraneous; large type; high contrast; top to
  bottom — question field + «Спросить» button, table «Ответ / Документ / Лист / Код /
  Проверено», «Подтверждение» panel with the fragment text, verdict line
  («Подтверждено N из N» / «Ответ отклонён — нет подтверждений на листах»); refusal shown
  explicitly and calmly, never like an error.
- Double-click on a table row opens the source sheet's stored text (modal dialog);
  missing sheet must degrade to a calm message.
- Constraints: PyQt5 only (no Qt Quick, no web assets), UTF-8 with Cyrillic, offline,
  portable; verification logic lives in `pdfgraph.gui.viewmodel`, the window stays a thin shell.
- `registry_path` is a reserved constructor parameter (not yet used). (from controller)

## Brand Commitments

Russian-language interface with the exact brief wording («Спросить», «Подтверждение»,
verdict strings). No existing visual identity to inherit: this is the first GUI of the
`pdfgraph` package; the legacy PyQt5 tool on master is a different generation and is not an
identity source. (verified from repo)

## Evidence on Hand

- Real verbatim OCR page text and snippet windows (≤200 chars) stored in the graph;
  test fixture graph (3 sheets, «РТЭ кн.1.pdf», codes 023.11.30 / 028.10.00) in `tests/conftest.py`.
- No photography, logos, or brand assets exist for this package — future work must not fabricate any.

## Product Principles

1. Verified or silent: only machine-checked citations are presented as knowledge; everything
   else is an explicit refusal.
2. Honest calm: refusal and missing sources are normal, neutrally-styled outcomes — never error styling.
3. Provenance one click away: every row names document, sheet, and system code; double-click
   shows the verbatim source text.
4. The tool disappears into the task: standard desktop affordances, dense readable table,
   one family, no decoration that doesn't carry state.

## Accessibility & Inclusion

High contrast and large type are brief-mandated (workshop lighting, Cyrillic technical text);
WCAG-AA-level text contrast (≥4.5:1 body, ≥3:1 large) is the working bar. (inferred from the
brief's "крупный шрифт, высокий контраст")
