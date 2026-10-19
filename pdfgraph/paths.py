"""Resolution of the graph store location across the three launch modes.

Real launch modes of the app (Stage 2 repro):

- ``python -m pdfgraph.gui.app`` from the repository root (development);
- ``dist\\pdfgraph\\pdfgraph.exe`` started directly — CWD is the exe
  directory, while the real ``graph.db`` sits in the repository root, two
  levels ABOVE the exe directory (this used to break the graph: «не ищет»);
- «Граф знаний.bat» — the launcher sets CWD to the project root first, so
  the old CWD-only lookup worked there by accident.

:func:`resolve_store_path` implements the task 2.0 priority order:

1. explicit ``--store`` (no silent fallback: a wrongly typed explicit path
   is reported, never rerouted);
2. the exe directory (PyInstaller ``sys.frozen``);
3. one and two levels above the exe directory;
4. the current working directory;
5. otherwise :class:`FileNotFoundError` listing EVERY tried path, so the
   caller can show a calm, specific message.
"""

import sys
from pathlib import Path

#: Single canonical file name of the SQLite graph store.
STORE_NAME = "graph.db"

#: How many levels above the exe dir to probe (task 2.0: 1–2).
MAX_EXE_UPLEVELS = 2


def resolve_store_path(
    explicit: str | None = None,
    *,
    exe_dir: str | None = None,
    cwd: str | None = None,
) -> str:
    """Return an existing path to the SQLite graph store.

    ``explicit`` is normally the user's ``--store`` argument: when given, it
    must exist and is returned as is — an explicit path is never silently
    replaced by an auto-discovered one. Without ``explicit`` the candidates
    are the exe directory (only for a frozen binary), up to
    :data:`MAX_EXE_UPLEVELS` levels above it, and the working directory.
    ``exe_dir``/``cwd`` override the derived values (tests inject synthetic
    layouts without touching ``sys.frozen``).

    Raises :class:`FileNotFoundError` with the full list of tried candidates
    when nothing exists — the message is shown to the user as is.
    """
    if explicit:
        # The explicit path is the contract: return it or fail, no fallback.
        path = Path(explicit)
        if path.is_file():
            return str(path)
        raise FileNotFoundError(
            f"{STORE_NAME} не найден; путь задан явно ({path}), "
            "автопоиск по умолчанию пропущен")

    candidates: list[Path] = []
    if exe_dir is not None:
        base = Path(exe_dir).resolve()
    elif getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = None
    if base is not None:
        for up in range(MAX_EXE_UPLEVELS + 1):
            candidates.append(base.parents[up - 1] / STORE_NAME
                              if up > 0 else base / STORE_NAME)
    working = Path(cwd) if cwd is not None else Path.cwd()
    candidates.append(working.resolve() / STORE_NAME)

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    for candidate in unique:
        if candidate.is_file():
            return str(candidate)
    listing = "\n".join(f"  - {candidate}" for candidate in unique)
    raise FileNotFoundError(
        f"{STORE_NAME} не найден; ожидался в одном из путей:\n{listing}")



# The candidate list for a base dir of ``<root>/dist/pdfgraph`` is:
#   <root>/dist/pdfgraph/graph.db   (up=0, beside the exe)
#   <root>/dist/graph.db            (up=1)
#   <root>/graph.db                 (up=2 — the repository root; mode B hit)
# plus ``<cwd>/graph.db`` as the last resort.