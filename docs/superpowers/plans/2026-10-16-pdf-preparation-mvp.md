# PDF Preparation (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a portable, zero-install Windows tool that turns a folder of scanned technical PDFs into searchable PDFs with a full text layer plus structured per-page data (system/subsystem code, blank-boundary flag), and a report of pages that need a better re-scan.

**Architecture:** A Python CLI package `pdfprepare`. Each PDF is opened with PyMuPDF; pages are classified native-text vs raster; raster pages are rendered, preprocessed, and OCR'd (Tesseract, rus+eng); system/subsystem codes are extracted from header/footer text; a searchable PDF and a JSON structure are written per document; low-confidence pages are listed for re-scan. No ocrmypdf/Ghostscript/poppler; Tesseract + tessdata are bundled with the app.

**Tech Stack:** Python 3.12, PyMuPDF (`pymupdf`), `pytesseract` + bundled `tesseract.exe` + `tessdata` (rus, eng), `Pillow`, `numpy`, `opencv-python-headless`, `pytest`, PyInstaller (one-folder).

**Spec:** `docs/superpowers/specs/2026-10-16-pdf-ocr-preparation-design.md` — the plan argues from the spec; executors read both.

## Global Constraints

- Windows-only, zero-install: the finished folder runs by copying it; Tesseract engine and `tessdata` (rus, eng) live inside the app folder (no `C:\Program Files\Tesseract-OCR`, no Ghostscript, no poppler).
- OCR languages: `"rus+eng"`.
- Do NOT flag intentionally blank pages as defects — a blank/near-blank page is a structural boundary; it is reported as `is_blank=True`, not as a quality failure.
- System/sub-system codes appear on every page; navigation key is the code, NOT the sheet number.
- Code registry file: `Коды систем Вертолета.txt` in the project root (ATA-100 style, `XX.00.00` = system, `XX.YY.00` = section).
- Output per document: (1) a searchable PDF preserving the visual fidelity of the scan (no degrading recompression) with a full text layer including table text, and (2) a JSON structure `system → subsystem → pages`.
- Preserve native text on pages/files that already have real text (route them around OCR).
- Third-level codes (`XX.YY.ZZ`) are stored verbatim; names come from the registry (levels 1–2) or the document text.

## Review Focus

1. A code split across lines or misread by OCR (e.g. `023.11.30` as `023.11 .30` or `О23.11.30` with a Cyrillic `О`) — code extraction must normalize whitespace/dots and tolerate a misread leading/trailing char, never crash.
2. A near-blank page that still carries a meaningful code (a section divider) — must be marked `is_blank` AND keep its code, and must NOT appear in the "needs re-scan" report.
3. A document/page with no detectable code at all — must not crash; page is recorded with `system_code=""` and still processed.
4. A native-text page passed through OCR would degrade it — the router must send native-text pages straight to output unchanged.
5. Cyrillic/Latin homoglyph normalization must not corrupt real codes (e.g. `023.10.00` must stay `023.10.00`, never become `O23.1O.OO`).

---

## File Structure

- `pdfprepare/__init__.py`
- `pdfprepare/models.py` — dataclasses `Word`, `PageInfo`, `DocResult`, `QualityFlag`.
- `pdfprepare/registry.py` — parse `Коды систем Вертолета.txt` → `{code: name}`.
- `pdfprepare/codes.py` — find/normalize system & subsystem codes in text.
- `pdfprepare/page.py` — render page to image; classify native-text vs raster; blank detection.
- `pdfprepare/preprocess.py` — grayscale, deskew, adaptive binarize, upscale.
- `pdfprepare/ocr.py` — run Tesseract, return `list[Word]`.
- `pdfprepare/output.py` — write searchable PDF (invisible text layer) and JSON structure.
- `pdfprepare/quality.py` — compute page confidence/coverage, produce `QualityFlag`s and the re-scan report.
- `pdfprepare/pipeline.py` — per-file orchestration (routing, OCR, structure, report).
- `pdfprepare/cli.py` — `argparse` entry point (`python -m pdfprepare <src> <dst>`).
- `pdfprepare/bundle.py` — locate/validate bundled `tesseract.exe` and `tessdata` (used by `ocr.py` and packaging).
- `tests/` — pytest suite; fixtures under `tests/fixtures/`.

---

### Task 1: Data models

**Files:**
- Create: `pdfprepare/models.py`
- Create: `pdfprepare/__init__.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `@dataclass Word: text: str; x0: float; y0: float; x1: float; y1: float; conf: float`
  - `@dataclass PageInfo: page_index: int; is_native_text: bool; is_blank: bool; system_code: str; subsystem_code: str; full_text: str; words: list[Word]`
  - `@dataclass DocResult: source_path: str; page_count: int; pages: list[PageInfo]; system_names: dict[str, str]`
  - `@dataclass QualityFlag: page_index: int; reason: str; avg_conf: float; text_coverage: float`

- [ ] **Step 1: Write the failing test**

```python
def test_models_construct_and_defaults():
    from pdfprepare.models import Word, PageInfo, DocResult, QualityFlag
    w = Word("А", 0.0, 0.0, 10.0, 10.0, 85.0)
    assert w.conf == 85.0 and w.text == "А"
    p = PageInfo(page_index=0, is_native_text=False, is_blank=True,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert p.is_blank is True and p.system_code == ""
    r = DocResult(source_path="a.pdf", page_count=1, pages=[p], system_names={})
    assert len(r.pages) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdfprepare.models'`

- [ ] **Step 3: Implement the four dataclasses in `pdfprepare/models.py`**

Plain `@dataclass` with the exact fields and defaults shown above. No methods.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/models.py pdfprepare/__init__.py tests/test_models.py
git commit -m "feat(models): add Word/PageInfo/DocResult/QualityFlag dataclasses"
```

---

### Task 2: Code registry parser

**Files:**
- Create: `pdfprepare/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `parse_registry(path: str) -> dict[str, str]` — maps normalized code → trimmed name. Ignores header rows (`Код ТО`, `Наименование`), chapter headings like `21 СИСТЕМА КОНДИЦИОНИРОВАНИЯ`, and `|` artifacts. Keys are `XX.XX.XX` strings with no leading/trailing spaces.

- [ ] **Step 1: Write the failing test**

```python
def test_parse_registry_sample(tmp_path):
    p = tmp_path / "codes.txt"
    p.write_text("021.00.00\tСИСТЕМА КОНДИЦИОНИРОВАНИЯ\n021.20.00\tраспределение\n023.11.30 | компоненты сети\n", encoding="utf-8")
    r = parse_registry(str(p))
    assert r["021.00.00"] == "СИСТЕМА КОНДИЦИОНИРОВАНИЯ"
    assert r["021.20.00"] == "распределение"
    assert r["023.11.30"] == "компоненты сети"

def test_parse_registry_skips_headers_and_chapters(tmp_path):
    p = tmp_path / "codes.txt"
    p.write_text("21 СИСТЕМА КОНДИЦИОНИРОВАНИЯ\nКод ТО\tНаименование\n021.00.00\tСИСТЕМА КОНДИЦИОНИРОВАНИЯ\n", encoding="utf-8")
    r = parse_registry(str(p))
    assert "21" not in r and "Код ТО" not in r and len(r) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL — `ImportError`/`NameError` on `parse_registry`

- [ ] **Step 3: Implement `parse_registry(path: str) -> dict[str, str]` in `pdfprepare/registry.py`**

Line-by-line: for each non-empty line, split on the first whitespace/`|` into `code` and `name`; keep only lines whose `code` matches `^\d{3}\.\d{2}\.\d{2}$`; strip trailing `|` from the name; `code.strip()` as key, `name.strip()` as value. Encoding `utf-8`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/registry.py tests/test_registry.py
git commit -m "feat(registry): parse ATA-100 code->name file"
```

---

### Task 3: Code extraction from page text

**Files:**
- Create: `pdfprepare/codes.py`
- Test: `tests/test_codes.py`

**Interfaces:**
- Produces:
  - `normalize_code(raw: str) -> str` — strip whitespace and non `[0-9.]` chars, collapse runs of spaces into none, return `""` if not `^\d{3}\.\d{2}\.\d{2}$`.
  - `system_of(code: str) -> str` — `"023.11.30"` → `"023.00.00"`; returns `""` for invalid input.
  - `extract_codes(text: str) -> list[str]` — unique normalized codes found, in document order.

- [ ] **Step 1: Write the failing test**

```python
def test_normalize_code_repairs_ocr_damage():
    assert normalize_code("023.11.30") == "023.11.30"
    assert normalize_code(" 023.11 .30 ") == "023.11.30"
    assert normalize_code("О23.11.30") == ""  # leading Cyrillic О -> rejected, not guessed
    assert normalize_code("O23.1O.OO") == ""  # homoglyphs rejected, never coerced
    assert normalize_code("023.10.00") == "023.10.00"  # a real code is never altered

def test_system_of():
    assert system_of("023.11.30") == "023.00.00"
    assert system_of("bad") == ""
    assert system_of("") == ""

def test_extract_codes_ordered_unique():
    text = "см. 028.10.00, рис. 1 ... 028.10.00 ... 023.00.00"
    assert extract_codes(text) == ["028.10.00", "023.00.00"]

def test_no_codes_returns_empty():
    assert extract_codes("нет кодов здесь") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_codes.py -v`
Expected: FAIL — import error on `pdfprepare.codes`

- [ ] **Step 3: Implement the three functions in `pdfprepare/codes.py`**

`extract_codes` uses `re.findall(r"\d{3}[.\s]*\d{2}[.\s]*\d{2}", text)` then `normalize_code` on each, dedup preserving order, drop empties.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_codes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/codes.py tests/test_codes.py
git commit -m "feat(codes): extract and normalize system/subsystem codes"
```

---

### Task 4: Page classification and blank detection

**Files:**
- Create: `pdfprepare/page.py`
- Test: `tests/test_page.py`

**Interfaces:**
- Produces:
  - `has_native_text(page: fitz.Page) -> bool` — `True` if `page.get_text("text").strip()` is non-empty and has ≥ 20 chars (native digital text), else `False`.
  - `render(page: fitz.Page, dpi: int = 300) -> PIL.Image.Image` — `page.get_pixmap(dpi=dpi)`, converted to `"L"`.
  - `is_blank(words: list[Word], page_rect: tuple[float,float,float,float]) -> bool` — `True` when `len(words) < 5` AND text-area coverage < 0.005.

- [ ] **Step 1: Write the failing test**

```python
from pdfprepare.models import Word
def test_is_blank_few_words_low_coverage():
    w = [Word("x", 0, 0, 8, 8, 80.0)]  # tiny, single word
    assert is_blank(w, (0, 0, 612, 792)) is True

def test_is_blank_many_words():
    ws = [Word(f"w{i}", 0, 0, 20, 20, 80.0) for i in range(10)]
    assert is_blank(ws, (0, 0, 612, 792)) is False

def test_has_native_text(tmp_path):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "ЭТО ПОЛНЫЙ ЦИФРОВОЙ ТЕКСТ СТРАНИЦЫ ДОСТАТОЧНОЙ ДЛИНЫ")
    assert has_native_text(page) is True
    doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_page.py -v`
Expected: FAIL — import error on `pdfprepare.page`

- [ ] **Step 3: Implement the three functions in `pdfprepare/page.py`**

Coverage = sum of word box areas ÷ page area. `render` uses `page.get_pixmap(dpi=dpi)` then `pix.tobytes("png")` into `PIL.Image.open(io.BytesIO(...)).convert("L")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_page.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/page.py tests/test_page.py
git commit -m "feat(page): classify native vs raster and detect blank pages"
```

---

### Task 5: Image preprocessing

**Files:**
- Create: `pdfprepare/preprocess.py`
- Test: `tests/test_preprocess.py`

**Interfaces:**
- Produces: `prepare(img: PIL.Image.Image, target_dpi: int = 300) -> PIL.Image.Image` — grayscale → upscale so the shorter side reaches `target_dpi` (if smaller) → deskew → adaptive binarization. Returns a `"L"` PIL image.
- Helper (also exposed for tests): `deskew_angle(img) -> float` using minAreaRect on thresholded non-white pixels; returns 0.0 on empty.

- [ ] **Step 1: Write the failing test**

```python
def test_prepare_returns_grayscale_and_upscales():
    from PIL import Image
    src = Image.new("RGB", (300, 400), "white")
    out = prepare(src, target_dpi=300)
    assert out.mode == "L"
    assert out.size[0] >= 300 and out.size[1] >= 400

def test_deskew_angle_level_image_is_zero():
    from PIL import Image
    img = Image.new("L", (200, 200), 255)
    assert abs(deskew_angle(img)) < 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preprocess.py -v`
Expected: FAIL — import error on `pdfprepare.preprocess`

- [ ] **Step 3: Implement `prepare` and `deskew_angle` in `pdfprepare/preprocess.py`**

Use `numpy`/`cv2` (`cv2.threshold(..., cv2.THRESH_OTSU)`), `cv2.getRotationMatrix2D` for deskew, and `PIL.Image.resize` with `LANCZOS` for upscale.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preprocess.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/preprocess.py tests/test_preprocess.py
git commit -m "feat(preprocess): grayscale/deskew/binarize/upscale scan pages"
```

---

### Task 6: OCR wrapper

**Files:**
- Create: `pdfprepare/ocr.py`
- Test: `tests/test_ocr.py`

**Interfaces:**
- Consumes: `Word` (Task 1), `prepare` (Task 5), `bundle.tesseract_cmd() -> str` and `bundle.tessdata_dir() -> str` (Task 6 also creates `bundle.py`).
- Produces: `ocr_image(img: PIL.Image.Image, lang: str = "rus+eng") -> list[Word]` — calls `pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT, config="--psm 6")`, returns one `Word` per detected word with non-empty text and `conf != -1`; `conf` = `conf/100.0`.
- Produces (same file): `ocr_available() -> bool` — `True` when `tesseract.exe` and `tessdata/rus.traineddata` + `tessdata/eng.traineddata` exist.

- [ ] **Step 1: Write the failing test (monkeypatched, no real Tesseract)**

```python
def test_ocr_image_maps_data_to_words(monkeypatch):
    import pdfprepare.ocr as ocr
    monkeypatch.setattr(ocr.pytesseract, "image_to_data",
        lambda *a, **k: {"level": [5], "text": ["ПРИМЕР"], "conf": [90],
                         "left": [0], "top": [0], "width": [100], "height": [20]})
    words = ocr.ocr_image(None)
    assert len(words) == 1
    w = words[0]
    assert w.text == "ПРИМЕР" and w.conf == 0.9 and w.x1 == 100.0 and w.y1 == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ocr.py -v`
Expected: FAIL — import error on `pdfprepare.ocr`

- [ ] **Step 3: Implement `ocr_image`, `ocr_available`, and `pdfprepare/bundle.py`**

`bundle.py` exposes `tesseract_cmd()` and `tessdata_dir()` pointing at a bundled `tesseract/` folder (app-dir-relative, with an env override `PDFPREPARE_TESSERACT`). `ocr_image` sets `pytesseract.pytesseract.tesseract_cmd` and `TESSDATA_PREFIX` from those before calling.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ocr.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/ocr.py pdfprepare/bundle.py tests/test_ocr.py
git commit -m "feat(ocr): pytesseract wrapper with bundled tesseract discovery"
```

---

### Task 7: Searchable PDF and JSON output

**Files:**
- Create: `pdfprepare/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes: `Word`, `PageInfo`, `DocResult` (Task 1).
- Produces:
  - `write_searchable_pdf(src_pdf: str, pages: list[PageInfo], out_path: str) -> None` — opens `src_pdf`, for each non-native page inserts each `Word` as invisible text (`insert_text(..., render_mode=3)` at the word's bbox, fontsize from box height), saves to `out_path`.
  - `write_json(result: DocResult, out_path: str) -> None` — writes `json.dumps(..., ensure_ascii=False, indent=2)` with keys `source_path`, `page_count`, `system_names`, and `pages` (each as a dict).

- [ ] **Step 1: Write the failing test (searchability is the assertion)**

```python
def test_write_searchable_pdf_contains_words(tmp_path):
    import fitz
    from pdfprepare.models import Word, PageInfo
    src = tmp_path / "s.pdf"; out = tmp_path / "o.pdf"
    d = fitz.open(); d.new_page(); d.save(str(src)); d.close()
    p = PageInfo(page_index=0, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="",
                 full_text="ПРИМЕР", words=[Word("ПРИМЕР", 72, 72, 130, 82, 0.9)])
    write_searchable_pdf(str(src), [p], str(out))
    d2 = fitz.open(str(out))
    assert "ПРИМЕР" in d2[0].get_text()
    d2.close()

def test_write_json_shape(tmp_path):
    from pdfprepare.models import PageInfo, DocResult
    r = DocResult(source_path="a.pdf", page_count=1,
                  pages=[PageInfo(page_index=0, is_native_text=True, is_blank=False,
                                  system_code="023.00.00", subsystem_code="023.11.30",
                                  full_text="", words=[])],
                  system_names={"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    out = tmp_path / "r.json"
    write_json(r, str(out))
    import json
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["pages"][0]["system_code"] == "023.00.00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_output.py -v`
Expected: FAIL — import error on `pdfprepare.output`

- [ ] **Step 3: Implement `write_searchable_pdf` and `write_json` in `pdfprepare/output.py`**

For the PDF writer, use a fresh `fitz.open(src_pdf)`; scale word bbox (PDF points already) directly; skip words whose box is empty.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_output.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/output.py tests/test_output.py
git commit -m "feat(output): write searchable PDF text layer and JSON structure"
```

---

### Task 8: Quality flags and re-scan report

**Files:**
- Create: `pdfprepare/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `PageInfo`, `DocResult`, `QualityFlag` (Task 1).
- Produces:
  - `MIN_CONF = 0.60`, `MIN_COVERAGE = 0.02` (module constants; confidence is 0..1).
  - `flag_pages(result: DocResult) -> list[QualityFlag]` — for each **non-blank, non-native** page: `avg_conf` = mean word conf, `text_coverage` = word area ÷ page area (page area from words' bbox or fixed 612×792 if no words); emit a flag when `avg_conf < MIN_CONF` OR `text_coverage < MIN_COVERAGE`, with reason `"low_confidence"` or `"low_text_coverage"`.
  - `write_report(flags: list[QualityFlag], out_path: str) -> None` — writes a UTF-8 text file, one line per flag: `page {page_index}: {reason} (conf={avg_conf:.2f}, coverage={text_coverage:.4f})`.

- [ ] **Step 1: Write the failing test**

```python
def test_flag_low_confidence_page():
    from pdfprepare.models import PageInfo, DocResult, Word
    p = PageInfo(page_index=2, is_native_text=False, is_blank=False,
                 system_code="", subsystem_code="",
                 full_text="", words=[Word("а", 0, 0, 10, 10, 0.4) for _ in range(8)])
    r = DocResult(source_path="a.pdf", page_count=3, pages=[p], system_names={})
    flags = flag_pages(r)
    assert flags and flags[0].page_index == 2 and flags[0].reason == "low_confidence"

def test_blank_page_not_flagged():
    from pdfprepare.models import PageInfo, DocResult
    p = PageInfo(page_index=0, is_native_text=False, is_blank=True,
                 system_code="", subsystem_code="", full_text="", words=[])
    assert flag_pages(DocResult("a.pdf", 1, [p], {})) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_quality.py -v`
Expected: FAIL — import error on `pdfprepare.quality`

- [ ] **Step 3: Implement `flag_pages` and `write_report` in `pdfprepare/quality.py`**

Straightforward; page area = bbox of the union of words when present, else 612×792.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_quality.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/quality.py tests/test_quality.py
git commit -m "feat(quality): flag low-confidence pages and write re-scan report"
```

---

### Task 9: Pipeline and CLI

**Files:**
- Create: `pdfprepare/pipeline.py`
- Create: `pdfprepare/cli.py`
- Create: `pdfprepare/__main__.py`
- Test: `tests/test_pipeline.py`
- Fixture: `tests/fixtures/two_page.pdf` (generated in-test via PyMuPDF: page 1 native text, page 2 raster image)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `process_file(src: str, dst_dir: str, registry: dict[str, str]) -> DocResult` — opens PDF, per page: classify native vs raster; native pages keep `full_text` from `get_text` and pass words through; raster pages: render→`prepare`→`ocr_image`→`list[Word]`; `full_text` = `" ".join(w.text)`. `system_code`/`subsystem_code` from `extract_codes` over the page's footer text (last 3 lines of the page text) and over the whole page as fallback: `system_code = system_of(first subsystem code)`, `subsystem_code = first subsystem-level code`. `system_names` = registry lookups of the codes found. Writes `out.pdf`, `out.json`, `out.report.txt` into `dst_dir`; returns the `DocResult`.
  - `main(argv: list[str]) -> int` — `argparse`: positional `src`, `dst`, `--registry` (default `Коды систем Вертолета.txt`); iterates `*.pdf` in `src` (non-recursive), calls `process_file`, prints a one-line summary per file, prints the re-scan report path; returns 0.

- [ ] **Step 1: Write the failing test (OCR mocked)**

```python
def test_process_file_builds_structure(tmp_path, monkeypatch):
    import fitz
    import pdfprepare.pipeline as pl
    src = tmp_path / "in.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 72), "ЭТО ЦИФРОВОЙ ТЕКСТ ДЛЯ НАТИВНОЙ СТРАНИЦЫ ДОСТАТОЧНОЙ ДЛИНЫ")
    d.new_page()  # raster (empty) page
    d.save(str(src)); d.close()

    monkeypatch.setattr(pl, "ocr_image",
        lambda img, lang="rus+eng": [__import__("pdfprepare.models", fromlist=["Word"]).Word("023.11.30", 72, 700, 140, 710, 0.95)])
    dst = tmp_path / "out"; dst.mkdir()
    result = pl.process_file(str(src), str(dst), {"023.00.00": "СВЯЗНОЕ РАДИООБОРУДОВАНИЕ"})
    assert result.pages[0].is_native_text is True
    assert result.pages[1].subsystem_code == "023.11.30"
    assert (dst / "in.json").exists() and (dst / "in.pdf").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — import error on `pdfprepare.pipeline`

- [ ] **Step 3: Implement `process_file` and `main` in `pdfprepare/pipeline.py` and `cli.py`**

Native pages are copied to the output text layer as-is (their original words); raster pages use the render→prepare→OCR chain. Footer extraction: split page text on newlines, take the last 3 non-empty lines.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pdfprepare/pipeline.py pdfprepare/cli.py pdfprepare/__main__.py tests/test_pipeline.py
git commit -m "feat(pipeline): per-file OCR pipeline with CLI entry point"
```

---

### Task 10: Portable packaging

**Files:**
- Create: `pyproject.toml` (deps: `pymupdf`, `pytesseract`, `pillow`, `numpy`, `opencv-python-headless`, `pytest`)
- Create: `packaging/` notes + `tesseract/` bundle script `scripts/bundle_tesseract.ps1`
- Create: `pdfprepare.spec` (PyInstaller one-folder, includes `tesseract/tesseract.exe` and `tesseract/tessdata/*.traineddata` as data)

**Interfaces:**
- Produces: a single folder `dist/pdfprepare/` that runs `pdfprepare.exe <src> <dst>` on a clean Windows PC with no installed Python/Tesseract/Ghostscript.

- [ ] **Step 1: Record the dependency list in `pyproject.toml` and install into the venv**

Run: `python -m pip install pymupdf pytesseract pillow numpy opencv-python-headless`
Expected: installs cleanly.

- [ ] **Step 2: Write `scripts/bundle_tesseract.ps1` that copies `tesseract.exe` and `tessdata` from the dev machine into `pdfprepare/tesseract/`**

Expected: script exists and, run on the dev machine, produces `pdfprepare/tesseract/tesseract.exe` and `pdfprepare/tesseract/tessdata/{rus,eng}.traineddata`.

- [ ] **Step 3: Verify `bundle.tesseract_cmd()` finds the bundled files**

Run: `python -c "from pdfprepare.bundle import tesseract_cmd, tessdata_dir; from pdfprepare.ocr import ocr_available; print(tesseract_cmd(), tessdata_dir(), ocr_available())"`
Expected: prints real paths; `ocr_available()` is `True` when files are present.

- [ ] **Step 4: Build the one-folder bundle with PyInstaller**

Run: `pyinstaller pdfprepare.spec`
Expected: `dist/pdfprepare/pdfprepare.exe` exists.

- [ ] **Step 5: Smoke-test the bundle on a copy of a real PDF**

Run: `dist/pdfprepare/pdfprepare.exe "pdfs" "pdfs_out"` on a single test PDF, then open the output and confirm text is selectable and `in.json`/`in.report.txt` exist.
Expected: selectable text and JSON present.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml pdfprepare.spec scripts/bundle_tesseract.ps1
git commit -m "feat(packaging): PyInstaller one-folder build with bundled Tesseract"
```

---

## Out of scope (Phase 2, separate plan)

Table structural extraction (rows/columns/cells as data) and the figure/schematic registry with system linkage — see spec sections 4.4 and 9. These are deferred to a follow-up plan; this MVP already produces a full searchable text layer including table text, and the page↔system structure.
