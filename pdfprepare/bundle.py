import os
import sys


def _bundle_root() -> str:
    """Directory that contains the bundled ``tesseract/`` folder.

    In a PyInstaller one-folder build the bundled data lives next to the frozen
    modules under ``sys._MEIPASS`` (the ``_internal`` directory); in a normal
    Python run it is the package directory.
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def tesseract_cmd() -> str:
    """Path to the bundled ``tesseract.exe``.

    Resolved relative to the package directory (or the frozen bundle's data
    directory), unless the environment variable ``PDFPREPARE_TESSERACT`` points
    at a tesseract installation directory (containing ``tesseract.exe`` and
    ``tessdata/``).
    """
    override = os.environ.get("PDFPREPARE_TESSERACT")
    base = override if override else os.path.join(_bundle_root(), "tesseract")
    return os.path.join(base, "tesseract.exe")


def tessdata_dir() -> str:
    """Path to the bundled ``tessdata/`` directory."""
    override = os.environ.get("PDFPREPARE_TESSERACT")
    base = override if override else os.path.join(_bundle_root(), "tesseract")
    return os.path.join(base, "tessdata")
