def test_figures_caption_and_dedup():
    from pdfprepare.figures import extract_figures
    text = "Рис. 12 Каталожный лист\nрис. 3\nРисунок 12 Повтор\nмусор"
    figs = extract_figures(text, page_index=4)
    assert [(f.number, f.caption) for f in figs] == [("12", "Каталожный лист"), ("3", "")]


def test_no_figures():
    from pdfprepare.figures import extract_figures
    assert extract_figures("нет рисунков", 0) == []
