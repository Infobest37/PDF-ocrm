"""Detect ruled tables and place OCR words into their cells.

All coordinates in this module are in the pixel coordinate space of ``img``.
In particular, ``Word`` boxes passed to :func:`extract_tables` and the
``Table.bbox`` values it returns must use that same image space. Consumers
that need PDF-point coordinates (such as the page pipeline) are responsible
for converting both using the same scale factor.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from pdfprepare.models import Table, Word


_HORIZONTAL_KERNEL = (60, 1)
_VERTICAL_KERNEL = (1, 60)
# Tesseract confidence is normalized to [0, 1]. Below 0.5 even one token makes
# a cell unsafe to use as a designation, so we retain the uncertainty flag but
# do not guess a possibly consequential part number.
MIN_CELL_CONF = 0.5


def _line_masks(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    gray = np.asarray(img.convert("L"))
    _, inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(
        inverted,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, _HORIZONTAL_KERNEL),
    )
    vertical = cv2.morphologyEx(
        inverted,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, _VERTICAL_KERNEL),
    )
    return horizontal, vertical


def _runs(mask: np.ndarray, axis: int, offset: int = 0) -> list[float]:
    """Return centers of contiguous line runs projected along one axis."""
    projection = np.count_nonzero(mask, axis=axis)
    indexes = np.flatnonzero(projection)
    if not len(indexes):
        return []

    groups = np.split(indexes, np.flatnonzero(np.diff(indexes) > 1) + 1)
    return [float(offset + (group[0] + group[-1]) / 2) for group in groups]


def _line_positions(
    horizontal: np.ndarray, vertical: np.ndarray, bbox: tuple[int, int, int, int]
) -> tuple[list[float], list[float]]:
    x0, y0, x1, y1 = bbox
    crop_h = horizontal[y0:y1, x0:x1]
    crop_v = vertical[y0:y1, x0:x1]
    # axis=1 collapses columns and leaves one score per row; axis=0 does the
    # converse. Morphological opening filters out text before this projection.
    ys = _runs(crop_h, axis=1, offset=y0)
    xs = _runs(crop_v, axis=0, offset=x0)
    return xs, ys


def detect_table_bboxes(img: Image.Image) -> list[tuple[float, float, float, float]]:
    """Find grid regions containing at least three rows and two columns.

    Detection uses OTSU binarization, horizontal and vertical morphological
    openings, and their intersections. Returned boxes are ``(x0, y0, x1, y1)``
    in pixels of the supplied image.
    """
    horizontal, vertical = _line_masks(img)
    grid = cv2.bitwise_or(horizontal, vertical)
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[tuple[float, float, float, float]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width < 2 * _HORIZONTAL_KERNEL[0] or height < 3 * _VERTICAL_KERNEL[1]:
            continue
        bbox = (x, y, x + width, y + height)
        xs, ys = _line_positions(horizontal, vertical, bbox)
        if len(xs) >= 3 and len(ys) >= 4:
            boxes.append(tuple(float(value) for value in bbox))

    return sorted(boxes, key=lambda box: (box[1], box[0]))


def detect_table_bzones(img: Image.Image) -> list[tuple[float, float, float, float]]:
    """Compatibility alias for table bounding-zone detection."""
    return detect_table_bboxes(img)


def extract_tables(img: Image.Image, words: list[Word], page_index: int) -> list[Table]:
    """Detect grids and return their cells populated from OCR ``words``.

    A word is assigned by its box center. Words in a clear cell are joined with
    one space in left-to-right order; a cell containing any word below
    :data:`MIN_CELL_CONF` is left empty and marked ambiguous instead of guessed.
    Cells without words remain exactly ``""`` and are not ambiguous. Input
    word boxes and returned table boxes use pixel coordinates of ``img``.
    """
    horizontal, vertical = _line_masks(img)
    boxes = detect_table_bboxes(img)
    tables: list[Table] = []

    for box in boxes:
        x0, y0, x1, y1 = (int(round(value)) for value in box)
        xs, ys = _line_positions(horizontal, vertical, (x0, y0, x1, y1))
        if len(xs) < 3 or len(ys) < 4:
            continue

        row_count = len(ys) - 1
        col_count = len(xs) - 1
        cells: list[list[list[Word]]] = [
            [[] for _ in range(col_count)] for _ in range(row_count)
        ]

        for word in words:
            center_x = (word.x0 + word.x1) / 2
            center_y = (word.y0 + word.y1) / 2
            if not (xs[0] < center_x < xs[-1] and ys[0] < center_y < ys[-1]):
                continue
            col = int(np.searchsorted(xs, center_x, side="right") - 1)
            row = int(np.searchsorted(ys, center_y, side="right") - 1)
            if 0 <= row < row_count and 0 <= col < col_count:
                cells[row][col].append(word)

        rows: list[list[str]] = []
        ambiguous: list[list[bool]] = []
        for row in cells:
            text_row = []
            ambiguous_row = []
            for cell in row:
                uncertain = bool(cell) and min(word.conf for word in cell) < MIN_CELL_CONF
                ambiguous_row.append(uncertain)
                text_row.append(
                    "" if uncertain else " ".join(
                        word.text for word in sorted(
                            cell, key=lambda item: (item.x0, item.y0)
                        )
                    )
                )
            rows.append(text_row)
            ambiguous.append(ambiguous_row)
        tables.append(Table(page_index=page_index, bbox=box, rows=rows,
                            ambiguous=ambiguous))

    return tables
