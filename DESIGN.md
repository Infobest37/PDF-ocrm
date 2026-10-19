---
name: Граф знаний Ми-8МТВ — job-aid GUI
description: Verified answers for helicopter tech documentation, rendered as a workshop job-aid card.
colors:
  ink: "#14181b"
  paper: "#f4f4f1"
  desk: "#31363a"
  pine: "#20644c"
  pine-deep: "#1a5240"
  pine-pressed: "#144132"
  graphite: "#575e63"
  rule: "#c9c6bc"
  selection: "#dde8e1"
  refusal-plate: "#454c52"
typography:
  verdict:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "17px"
    fontWeight: 600
  input:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "17px"
    fontWeight: 400
  body:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: "1.4"
  label:
    fontFamily: "Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 600
rounded:
  none: "0px"
spacing:
  cell: "6px 8px"
  zone: "16px"
  card: "24px"
components:
  button-ask:
    backgroundColor: "{colors.pine}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "10px 26px"
  button-ask-hover:
    backgroundColor: "{colors.pine-deep}"
  button-ask-pressed:
    backgroundColor: "{colors.pine-pressed}"
  button-ask-disabled:
    backgroundColor: "{colors.rule}"
    textColor: "{colors.graphite}"
  button-close:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    padding: "9px 22px"
  input-question:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "9px 11px"
  verdict-plate:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "12px 14px"
  verdict-plate-refusal:
    backgroundColor: "{colors.refusal-plate}"
    textColor: "{colors.paper}"
---

# Design System: Граф знаний Ми-8МТВ (job-aid GUI)

## Overview

**Creative North Star: "The Workshop Job-Aid Card"**

The answer screen is a printed instruction card pinned at the workstation: paper-white surface on a slate desk, ink-black type only, and exactly one reversed plate — the verdict — carrying the screen's authority. The interface is built for an aviation engineer reading Cyrillic technical text under bright workshop light, so contrast and size are obligations, not preferences. Nothing is decorative; every colored thing is doing a job (action, selection, confirmation, verdict).

The system is flat and ruled: 1px hairlines as the only separators, zero radius everywhere, integer pixel sizes only. Depth comes from tonal layering — desk (#31363a) under card (#f4f4f1) under plate (#14181b) — never from shadows. The single accent, pine green, appears only where the product confirms something: the primary action, the selection, and the verified stamp.

**Key Characteristics:**
- Paper card on slate desk; ink-on-paper text at every level.
- One reversal per screen: the verdict plate.
- State as a stamp in a fixed cell (filled/hollow square), never as text or color alone.
- Refusal is the same plate voice in graphite — calm, never red.

## Colors

Neutral ink-on-paper with one working accent; color is state, never ornament.

### Primary
- **Pine Workmark** (#20644c): the confirmation accent — «Спросить» button, the filled verified stamp, cell selection context, and the focused input border. Deepens on hover (#1a5240) and press (#144132).

### Neutral
- **Ink** (#14181b): all text, all hairlines, and the confirmed verdict plate.
- **Paper** (#f4f4f1): card and control surfaces; text color on the ink plate.
- **Desk Slate** (#31363a): window ground around the card; the dialog's ground.
- **Graphite** (#575e63): unverified hollow stamp and awaiting hints — the calm secondary voice.
- **Rule** (#c9c6bc): table gridlines and disabled fills.
- **Selection Tint** (#dde8e1): pine's lightest wash, for selected rows only.
- **Refusal Plate** (#454c52): the verdict bar's graphite mode for refusals — visibly a verdict, deliberately not alarming.

### Named Rules
**The One Voice Rule.** Pine is used only where the product confirms or acts; it never decorates.
**The Calm-No-Red Rule.** Refusals, unverified rows and missing sheets render in graphite/ink tones; red is not part of this system.

## Typography

**Body Font:** Segoe UI (native Windows UI stack; the platform voice is deliberate for an Operate screen).
**Label Font:** same family, heavier.

**Character:** one workhorse sans carrying everything — hierarchy by weight and rule, not by face or size variety. All sizes are integer px (pixel-honest rendering). Cyrillic first-class.

### Hierarchy
- **Verdict** (600, 17px): the plate line — «Подтверждено N из N» / refusal text, paper on ink.
- **Input** (400, 17px): the question field, the largest interactive text (крупный шрифт, brief-pinned).
- **Body** (400, 15px): table cells, snippet panel, sheet dialog.
- **Label** (600, 13px): window title, table headers, section captions («Подтверждение»).

### Named Rules
**The Fixed Ramp Rule.** 13/15/17 only; new surfaces pick from these steps, never invent sizes.

## Layout

One card with 20px desk margin (frame) and 24px inner padding (vertical layout: 16px between zones, 6–10px within groups). Four pinned zones top-to-bottom: question row (field + button, 10px gap) → answers table (stretch, ~55%) → «Подтверждение» panel → verdict plate (full-width, 12px/14px padding). Window 1040×700 default, 880×600 minimum; table columns: Ответ stretches, Документ 180, Лист 70, Код 110, Проверено 110. Row heights track wrapped content via resizeRowsToContents and recompute on window resize.

## Elevation & Depth

Flat tonal layering: desk under card under plate. There are no box shadows in this system; depth is conveyed by surface tone steps and 1px ink borders. Focus is drawn as a 2px pine border (with padding compensated to hold position), the only thickness exception.

## Shapes

Zero radius on every surface — buttons, fields, table, panels, dialogs, and stamp marks are right angles with straight 1px ink rules. The verified/unverified mark is a 14px square drawn with QPainter in the «Проверено» column: filled pine = verified, hollow graphite = not; it sits in its fixed cell, centered, and never moves or changes size.

## Components

### Buttons
- **Shape:** square corners (0px).
- **Primary («Спросить»):** pine fill, paper text 17px/600, padding 10px 26px; hover pine-deep, pressed pine-pressed, disabled rule-fill with graphite text. It is the window's default button.
- **Secondary («Закрыть» in dialogs):** paper fill, ink text, 1px ink border; hover fills with the selection tint.

### Inputs / Fields
- **Style:** paper background, 1px ink border, 17px text, padding 9px 11px; placeholder in graphite.
- **Focus:** 2px pine border with padding reduced by 1px to keep geometry.
- **Error/Disabled:** none by design — an unanswered question is a refusal, not an invalid input.

### Tables
- **Style:** paper background, 1px rule (#c9c6bc) gridlines, 1px ink outer border; header sections are paper with 600 weight and a 1px ink bottom rule.
- **Selection:** full-row pine wash (#dde8e1) with ink text (palette Highlight/HighlightedText set explicitly); single row, not editable.
- **Cells:** 6px 8px padding; the answer column wraps with top alignment and row height auto-fits wrapped text.

### Verdict Plate (signature)
Full-width QLabel, 17px/600. Three closed states by dynamic property: awaiting (transparent, graphite hint), plate (ink background, paper text — answered), refusal (graphite-plate background, paper text — same size, same voice, no red).

### Sheet Dialog
Modal QDialog on the desk ground with a paper card (16/12px padding), a read-only verbatim text pane (15px), and a secondary Закрыть button. Missing sheets render a calm sentence naming the document and sheet; empty sheets state the domain fact: it is a section divider, not a scan defect.

## Do's and Don'ts

### Do:
- **Do** verify before displaying: the «Проверено» stamp is only ever the color of a machine-checked citation.
- **Do** keep the four pinned zones in the pinned order on every future variant of this window.
- **Do** render refusals and absences in graphite/ink tones with the same plate geometry as confirmations.
- **Do** use the fixed ramp (13/15/17) and integer paddings on new screens.

### Don't:
- **Don't** add red, orange or green traffic-light semantics to refusals or unverified rows.
- **Don't** use Unicode glyphs or emoji as the state mark; the stamp is painted (QPainter) and stays a filled/hollow square.
- **Don't** round any corner, soften any edge with blur, or introduce shadow.
- **Don't** decorate with pine; if a pine thing isn't confirmable, it doesn't ship.
