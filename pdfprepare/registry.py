import re

_CODE_RE = re.compile(r"^\d{3}\.\d{2}\.\d{2}$")


def parse_registry(path: str) -> dict[str, str]:
    """Parse an ATA-100 code registry file into a code -> name mapping.

    Each line is either ``CODE<whitespace|pipe>NAME`` or a header/chapter
    heading to be ignored. Codes must match ``XXX.XX.XX``; names have any
    trailing ``|`` artifact stripped.
    """
    result: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = re.split(r"[\s|]+", line, maxsplit=1)
            if len(parts) != 2:
                continue
            code, name = parts
            if not _CODE_RE.match(code):
                continue
            result[code.strip()] = name.rstrip("|").strip()
    return result
