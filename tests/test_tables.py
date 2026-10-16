from PIL import Image, ImageDraw


def _grid_image(rows, cols, texts):
    img = Image.new("L", (600, 200 * rows), "white")
    d = ImageDraw.Draw(img)
    for r in range(rows + 1):
        d.line((10, 10 + r * 190, 590, 10 + r * 190), fill="black", width=3)
    for c in range(cols + 1):
        d.line((10 + c * 190, 10, 10 + c * 190, 10 + rows * 190), fill="black", width=3)
    for (r, c), txt in texts.items():
        d.text((30 + c * 190, 40 + r * 190), txt, fill="black")
    return img


def test_detect_and_extract_three_by_three():
    from pdfprepare.tables import detect_table_bboxes, extract_tables
    from pdfprepare.models import Word

    img = _grid_image(
        3,
        3,
        {(0, 0): "N", (0, 1): "ОБОЗНАЧЕНИЕ", (1, 0): "3", (1, 1): "8МТ.6101.100"},
    )
    boxes = detect_table_bboxes(img)
    assert len(boxes) == 1
    words = [Word("8МТ.6101.100", 220, 220, 400, 240, 0.95)]
    tables = extract_tables(img, words, page_index=7)
    assert len(tables) == 1 and len(tables[0].rows) == 3
    assert tables[0].page_index == 7
    assert tables[0].rows[1][1] == "8МТ.6101.100"


def test_empty_cell_is_not_guessed():
    from pdfprepare.tables import extract_tables

    img = _grid_image(3, 2, {(0, 0): "N"})
    tables = extract_tables(img, [], page_index=0)
    assert tables[0].rows[0][1] == "" and tables[0].rows[1][0] == ""


def test_no_grid_returns_empty():
    from pdfprepare.tables import detect_table_bboxes

    assert detect_table_bboxes(Image.new("L", (400, 200), "white")) == []


def test_detection_ignores_regions_smaller_than_three_rows_or_two_columns():
    from pdfprepare.tables import detect_table_bboxes

    assert detect_table_bboxes(_grid_image(2, 2, {})) == []


def test_words_are_assigned_by_center_and_joined_in_reading_order():
    from pdfprepare.models import Word
    from pdfprepare.tables import extract_tables

    img = _grid_image(3, 2, {})
    words = [
        Word("right", 220, 220, 270, 240, 0.9),
        Word("left", 205, 220, 220, 240, 0.9),
    ]
    tables = extract_tables(img, words, page_index=3)
    assert tables[0].rows[1][1] == "left right"
    assert tables[0].rows[0][0] == ""
