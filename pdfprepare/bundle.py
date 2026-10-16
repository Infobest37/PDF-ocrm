import os

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def tesseract_cmd() -> str:
    """Path to the bundled ``tesseract.exe``.

    Resolved relative to the package directory, unless the environment
    variable ``PDFPREPARE_TESSERACT`` points at a tesseract installation
    directory (containing ``tesseract.exe`` and ``tessdata/``).
    """
    override = os.environ.get("PDFPREPARE_TESSERACT")
    base = override if override else os.path.join(_PACKAGE_DIR, "tesseract")
    return os.path.join(base, "tesseract.exe")


def tessdata_dir() -> str:
    """Path to the bundled ``tessdata/`` directory."""
    override = os.environ.get("PDFPREPARE_TESSERACT")
    base = override if override else os.path.join(_PACKAGE_DIR, "tesseract")
    return os.path.join(base, "tessdata")
