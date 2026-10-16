import re

_CODE_RE = re.compile(r"^\d{3}\.\d{2}\.\d{2}$")
_CODE_FIND_RE = re.compile(r"\d{3}[.\s]*\d{2}[.\s]*\d{2}")


def normalize_code(raw: str) -> str:
    """Strip everything but digits and dots, then accept only an exact
    ``XXX.XX.XX`` code. Anything else (homoglyphs, OCR damage) yields ``""``
    rather than a guessed code."""
    cleaned = re.sub(r"[^0-9.]", "", raw)
    if not _CODE_RE.match(cleaned):
        return ""
    return cleaned


def system_of(code: str) -> str:
    """Map a full code to its system-level code (``XXX.XX.XX`` -> ``XXX.00.00``)."""
    if not _CODE_RE.match(code):
        return ""
    return code[:3] + ".00.00"


def extract_codes(text: str) -> list[str]:
    """Return unique normalized codes found in ``text``, in document order."""
    codes: list[str] = []
    seen: set[str] = set()
    for match in _CODE_FIND_RE.findall(text):
        code = normalize_code(match)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes
