"""Task 2.0-fix: ``resolve_store_path`` must find the real graph.db for frozen exe.

Mode B of the Stage-2 repro: the GUI exe started directly from
``dist\\pdfgraph`` has no ``graph.db`` in CWD, while the real store sits at
the repository root (two levels above the exe dir). Before the fix there is
``pdfgraph.paths`` at all — the import fails and the test is red.
"""
import pytest


def test_store_opens_from_exe_dir(tmp_path):
    """CWD=dist/pdfgraph, graph.db two levels up -> resolves to repo root db."""
    from pdfgraph.paths import resolve_store_path

    root = tmp_path / "repo"
    exe_dir = root / "dist" / "pdfgraph"
    exe_dir.mkdir(parents=True)
    (root / "graph.db").write_bytes(b"SQLite format 3\x00")  # existence is what matters
    got = resolve_store_path(exe_dir=str(exe_dir), cwd=str(exe_dir))
    assert got == str(root / "graph.db")


def test_explicit_existing_store_beats_defaults(tmp_path):
    """An explicitly passed --store always wins over exe-dir/CWD candidates."""
    from pdfgraph.paths import resolve_store_path

    root = tmp_path / "repo"
    exe_dir = root / "dist" / "pdfgraph"
    exe_dir.mkdir(parents=True)
    (root / "graph.db").write_bytes(b"x")
    custom = tmp_path / "custom.db"
    custom.write_bytes(b"x")
    got = resolve_store_path(str(custom), exe_dir=str(exe_dir), cwd=str(exe_dir))
    assert got == str(custom)


def test_missing_everywhere_raises_with_tried_paths(tmp_path):
    """Store absent from every candidate -> FileNotFoundError with the list."""
    from pdfgraph.paths import resolve_store_path

    exe_dir = tmp_path / "dist" / "pdfgraph"
    exe_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError) as caught:
        resolve_store_path(exe_dir=str(exe_dir), cwd=str(tmp_path / "cwd"))
    message = str(caught.value)
    assert "graph.db" in message and "не найден" in message
    assert str(exe_dir) in message      # tried paths are named, not swallowed