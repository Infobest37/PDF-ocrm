"""Г2: красивый ответ для объяснительных вопросов — слой показа.

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

- ``pdfgraph.humanize.is_explain_question``: маркеры «для чего», «зачем»,
  «что делает», «что такое», «как устроен», «назначение»; вопрос-значение
  имеет приоритет; процедурные («как заменить») — не explain;
- ``pdfgraph.humanize.explain_display_items``: предложения назначения
  (объект вопроса + глагол назначения: «предназначен», «служит»,
  «обеспечивает», «является», «входит в состав», «используется для»,
  «включает», «выполняет функцию/роль», «применяется», «относится к»),
  до 4 идущих подряд, ≤ 400 символов; табличные обломки
  (болты/шайбы/«поз.») и показ-мусор («Копия документа», «Распечатал»,
  ГОСТ) в показ не идут; без глаголов назначения — первый содержательный
  кусок текста (fallback);
- ``pdfgraph.humanize.explain_display``: словарь показа с ключами Г1
  (items/source/source_url/quote) + ``note`` (пометка fallback или
  честная фраза); None — не explain-вопрос или отказ;
- ``pdfgraph.humanize.no_description_message``: «На этом листе описание
  не нашёл. Ближайшие листы: …».

Claim.text, verify и pages.text не трогаются. Живые тесты требуют
graph.db в корне (как tests/test_humanize_g1.py).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STORE_SOURCE = REPO_ROOT / "graph.db"

Q_IM47 = "для чего нужен ИМ-47"
Q_IM3A = "что делает ИМ-3А"
Q_AUTOPILOT = "как устроен автопилот"
Q_VALUE = "какое давление масла в двигателе ТВ3-117"
Q_PROCEDURAL = "как заменить масло в двигателе"


# --- Фейки ---------------------------------------------------------------

class _FakeStore:
    """store.page_text без БД: один текст на любой лист."""

    def __init__(self, text: str):
        self._text = text

    def page_text(self, document: str, page: int) -> str:
        return self._text


def _fake_answer(question: str) -> SimpleNamespace:
    return SimpleNamespace(
        refusal=False,
        question=question,
        fragments=[SimpleNamespace(document="РЭ1 кн. 1.pdf", page=198)],
        claims=[SimpleNamespace(text="дословная цитата")],
        neighbors=[{"document": "РЭ1 кн. 1.pdf", "page": 197,
                    "label": "предыдущий лист"}],
    )


# --- Юнит-тесты ----------------------------------------------------------

def test_is_explain_question_markers():
    from pdfgraph.humanize import is_explain_question

    for question in (Q_IM47, Q_IM3A, Q_AUTOPILOT, "зачем нужен кран 765",
                     "что такое БВК", "назначение блока БВК-777"):
        assert is_explain_question(question), question


def test_is_explain_question_negatives():
    from pdfgraph.humanize import is_explain_question

    assert not is_explain_question(Q_VALUE), "вопрос-значение приоритетен"
    assert not is_explain_question(Q_PROCEDURAL), "процедура — не explain"


def test_explain_items_purpose_sentences():
    from pdfgraph.humanize import explain_display_items

    text = "\n".join([
        "Копия документа № 5 от 12.03.98",
        "Распечатал Кирячков Андрей Григорьевич",
        "",
        "Исполнительный механизм ИМ-47 предназначен для управления",
        "топливным краном. Механизм служит для открытия и закрытия",
        "крана подачи топлива. Кран входит в состав топливной системы.",
        "",
        "1. Снимите болты крепления кожуха",
        "2. Проверьте шайбы поз. 4",
        "Болт АН-4 4",
        "Шайба 2",
        "поз. 4",
        "ГОСТ 2145-77",
        "Контрольная копия хранится в деле",
    ])
    items, purpose_found = explain_display_items(
        _FakeStore(text), Q_IM47, "РЭ1", 198)
    assert purpose_found, "предложения назначения должны найтись"
    assert items, "пункт показа не должен быть пуст"
    joined = " ".join(items)
    assert "предназначен" in joined.lower()
    assert len(joined) <= 400
    for forbidden in ("снимите", "болт", "шайб", "поз.", "копия",
                      "распечатал", "гост", "контрольная"):
        assert forbidden not in joined.lower(), forbidden


def test_explain_items_purpose_extended_verbs():
    from pdfgraph.humanize import explain_display_items

    text = ("Блок БВК-777 включает в себя усилитель сигналов. "
            "Он выполняет функцию распределения питания. "
            "Усилитель применяется на всех режимах. "
            "Прибор относится к группе второго контроля. "
            "Устройство используется для стабилизации параметров.")
    items, purpose_found = explain_display_items(
        _FakeStore(text), "что делает БВК-777", "РЭ1", 55)
    assert purpose_found
    assert len(items) >= 1


def test_explain_items_fallback_without_purpose():
    from pdfgraph.humanize import explain_display_items

    text = "\n".join([
        "Копия документа № 5",
        "1. Снимите кожух и болты",
        "2. Проверьте шайбы поз. 4",
        "Стабилизатор управляется педалями летчика.",
        "Усилие передается через тросовую проводку к смесителю.",
    ])
    items, purpose_found = explain_display_items(
        _FakeStore(text), "что делает стабилизатор", "РЭ1", 63)
    assert not purpose_found, "глаголов назначения в тексте нет"
    assert items, "fallback — первый содержательный кусок, не пустота"
    joined = " ".join(items)
    assert "Стабилизатор" in joined
    assert "Снимите" not in joined
    assert "шайбы" not in joined.lower()


def test_explain_display_fallback_note():
    from pdfgraph.humanize import explain_display

    text = "Стабилизатор управляется педалями летчика."
    display = explain_display(
        _FakeStore(text), _fake_answer("что делает стабилизатор"))
    assert display is not None
    assert display["items"]
    assert display["note"] == ("Точного описания назначения на листе "
                               "не нашёл. Вот что там есть:")
    assert display["quote"] == "дословная цитата"
    assert "лист 198" in display["source"]


def test_purpose_tier_priority_prednaznachen_first():
    """Г2в п.3: «предназначен» — высший приоритет перед «проводит».

    Объект вопроса в обоих предложениях; окно строится вперёд от якоря,
    поэтому пункт начинается с предложения «предназначен».
    """
    from pdfgraph.humanize import explain_display_items

    text = (
        "Блок БСПИ-4-2 проводит преобразование параметров полета "
        "в цифровой код. Блок БСПИ-4-2 предназначен для управления "
        "краном подачи топлива. Блок установлен на двигателе.")
    items, purpose_found = explain_display_items(
        _FakeStore(text), "для чего нужен БСПИ-4-2", "РЭ", 10)
    assert purpose_found
    joined = " ".join(items)
    assert "предназначен" in joined
    assert "проводит" not in joined.lower()


def test_purpose_composition_filter():
    """Г2в п.4: объект в перечислении («входят», «включают», «состоит
    из») — состав, не назначение; пункт берётся из глагола работы."""
    from pdfgraph.humanize import explain_display_items

    # Структура л.138 РЭ.РО Д-1263: составное перечисление и якорь
    # «проводит преобразование» с разрезом «сер. / 2, проводит…».
    text = (
        "Б о р т о в о е устройство. В состав бортового устройства "
        "регистрации входят следующие блоки и устройства: блок сбора "
        "полетной информации БСПИ-4-2 сер. "
        "2, проводит преобразование параметров полета, поступающих "
        "от датчиков в цифровой код с последующей регистрацией его "
        "в модуле памяти ЗБН-1-3 сер.")
    items, purpose_found = explain_display_items(
        _FakeStore(text), "для чего нужен БСПИ-4-2 сер. 2", "РЭ", 138)
    assert purpose_found
    joined = " ".join(items).lower()
    assert "проводит преобразование" in joined
    assert "в состав бортового устройства регистрации входят" not in joined


def test_purpose_composition_only_not_purpose():
    """Только перечисление состава — связки назначения нет (Г2в п.1)."""
    from pdfgraph.humanize import explain_display_items

    text = ("В состав бортового устройства регистрации входят "
            "следующие блоки: блок сбора полетной информации "
            "БСПИ-4-2 сер. 2 и пульт управления ПУ-25-1.")
    _items, purpose_found = explain_display_items(
        _FakeStore(text), "для чего нужен БСПИ-4-2", "РЭ", 138)
    assert not purpose_found, "«входят…» — состав, а не назначение"


def test_purpose_work_verbs():
    """Г2в п.2: новые глаголы работы агрегата — тир-2."""
    from pdfgraph.humanize import explain_display_items

    for tail in ("проводит преобразование сигналов в код.",
                 "измеряет давление масла на входе.",
                 "формирует сигнал управления.",
                 "выдает команду на табло.",
                 "выдаёт команду на табло.",
                 "регистрирует параметры полета.",
                 "преобразует сигнал в код.",
                 "выполняет функции питания датчиков."):
        text = f"Прибор А-1 {tail}"
        items, purpose_found = explain_display_items(
            _FakeStore(text), "что делает А-1", "РЭ", 5)
        assert purpose_found, tail


def test_explain_display_empty_sheet_honest_message():
    from pdfgraph.humanize import explain_display

    display = explain_display(
        _FakeStore("Копия документа № 5\nпоз. 4"),
        _fake_answer("что делает ИМ-3А"))
    assert display is not None
    assert display["items"] == []
    assert display["note"].startswith("На этом листе описание не нашёл")
    assert "лист 197" in display["note"], "ближайшие листы называются"


def test_explain_display_none_for_value_question():
    from pdfgraph.humanize import explain_display

    text = "Давление масла предназначено для смазки узлов."
    assert explain_display(
        _FakeStore(text), _fake_answer(Q_VALUE)) is None


def test_explain_display_none_for_refusal():
    from pdfgraph.humanize import explain_display

    answer = _fake_answer(Q_IM47)
    answer.refusal = True
    assert explain_display(_FakeStore("текст"), answer) is None


def test_no_description_message():
    from pdfgraph.humanize import no_description_message

    message = no_description_message([
        {"document": "РЭ1.pdf", "page": 63, "label": "предыдущий лист"},
    ])
    # Формат «лист N» — как в Г1 no_norm_message (без имени документа).
    assert message == ("На этом листе описание не нашёл. "
                       "Ближайшие листы: лист 63 (предыдущий лист).")
    assert no_description_message([]) == "На этом листе описание не нашёл."


# --- Живые тесты (graph.db в корне) --------------------------------------

def _load_golden_metrics():
    import importlib.util
    import sys

    path = REPO_ROOT / "tests" / "golden" / "metrics.py"
    spec = importlib.util.spec_from_file_location("golden_metrics_g2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def store_copy_g2(tmp_path_factory):
    if not STORE_SOURCE.is_file():
        pytest.skip("graph.db отсутствует в корне проекта")
    metrics = _load_golden_metrics()
    dest = tmp_path_factory.mktemp("g2_store") / "graph.db"
    metrics.copy_store(STORE_SOURCE, dest)
    return dest


def _answer(store, question):
    from pdfgraph.answer import build_answer

    return build_answer(store, question, None)


def _g2_display(store, question):
    from pdfgraph.humanize import explain_display

    return explain_display(store, _answer(store, question))


def test_live_im47_display(store_copy_g2):
    """«для чего нужен ИМ-47» → показ с листа 198, ≤400, назначение.

    Лидер ранжирования — л.83 (упоминание ИМ-47 в процедуре ЭРД);
    слой показа Г2 обязан взять назначение с л.198 (второй фрагмент
    топ-5), где «предназначен» и «ИМ-47» в связке идущих подряд
    предложений. Поиск не тронут.
    """
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    store = Store(str(store_copy_g2))
    try:
        answer = _answer(store, Q_IM47)
        display = _g2_display(store, Q_IM47)
        checks = verify_citations(answer, store)
    finally:
        store.close()
    assert display is not None, "explain-показ должен применяться"
    assert "лист 198" in display["source"], "назначение ИМ-47 — л.198"
    assert display["items"], "назначение должно найтись"
    joined = " ".join(display["items"])
    assert len(joined) <= 400
    assert "предназначен" in joined.lower()
    assert "им-47" in joined.lower()
    # Точная фраза «исполнительный механизм» на л.198 отсутствует в
    # pages.text (только «исполнительным механизмом» и OCR-перенос
    # «исполнительном ме-ханизма»); показ дословный, поэтому проверка —
    # по общей основе слова.
    assert "исполнительн" in joined.lower()
    assert checks and all(check.ok for check in checks), "verify зелёный"


def test_live_im3a_no_table(store_copy_g2):
    """«что делает ИМ-3А» — нет табличных обломков; описание или честно."""
    from pdfgraph.store import Store

    store = Store(str(store_copy_g2))
    try:
        display = _g2_display(store, Q_IM3A)
    finally:
        store.close()
    assert display is not None
    joined = " ".join(display["items"]).lower()
    assert "болт" not in joined
    assert "шайб" not in joined
    assert "поз." not in joined
    if not display["items"]:
        assert display["note"].startswith(
            "На этом листе описание не нашёл")


def test_live_autopilot_display(store_copy_g2):
    """«как устроен автопилот» — короткий показ без портянки и мусора."""
    from pdfgraph.store import Store

    store = Store(str(store_copy_g2))
    try:
        display = _g2_display(store, Q_AUTOPILOT)
    finally:
        store.close()
    assert display is not None
    joined = " ".join(display["items"]).lower()
    assert len(joined) <= 400
    for forbidden in ("копия документа", "распечатал", "гост "):
        assert forbidden not in joined, forbidden


def test_live_claim_text_verbatim(store_copy_g2):
    """Claim.text дословный: вхождение в pages.text не нарушено."""
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    store = Store(str(store_copy_g2))
    try:
        answer = _answer(store, Q_IM47)
        claim_text = answer.claims[0].text if answer.claims else ""
        page_text = store.page_text(answer.fragments[0].document,
                                    answer.fragments[0].page)
        checks = verify_citations(answer, store)
    finally:
        store.close()
    assert answer.claims, "цитата должна быть"
    assert claim_text in page_text, "цитата — дословный фрагмент листа"
    assert checks and all(check.ok for check in checks)


def test_live_bspi_display_no_composition(store_copy_g2):
    """«для чего нужен БСПИ-4-2 сер. 2» → л.138/139, «проводит
    преобразование», состав «в состав… входят…» в показ не идёт."""
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    store = Store(str(store_copy_g2))
    try:
        answer = _answer(store, "для чего нужен БСПИ-4-2 сер. 2")
        display = _g2_display(store, "для чего нужен БСПИ-4-2 сер. 2")
        checks = verify_citations(answer, store)
    finally:
        store.close()
    assert display is not None
    assert ("лист 138" in display["source"]
            or "лист 139" in display["source"]), "лид — л.138/139"
    assert display["items"], "назначение должно найтись"
    joined = " ".join(display["items"]).lower()
    assert len(joined) <= 400
    assert "проводит преобразование" in joined
    assert "параметров полет" in joined
    assert ("в состав бортового устройства регистрации входят"
            not in joined)
    assert checks and all(check.ok for check in checks)


def test_live_das_display(store_copy_g2):
    """«для чего нужен ДАС» — «предназначен для измерения приборной
    скорости» не сломался (Г2в)."""
    from pdfgraph.store import Store
    from pdfgraph.verify import verify_citations

    store = Store(str(store_copy_g2))
    try:
        display = _g2_display(store, "для чего нужен ДАС")
        answer = _answer(store, "для чего нужен ДАС")
        checks = verify_citations(answer, store)
    finally:
        store.close()
    assert display is not None
    assert display["items"]
    joined = " ".join(display["items"]).lower()
    assert len(joined) <= 400
    assert "предназначен" in joined
    assert "приборной скорости" in joined
    assert checks and all(check.ok for check in checks)


def test_live_mu615a_display(store_copy_g2):
    """«для чего нужен МУ-615А» — не сломалось (Г2в)."""
    from pdfgraph.store import Store

    store = Store(str(store_copy_g2))
    try:
        display = _g2_display(store, "для чего нужен МУ-615А")
    finally:
        store.close()
    assert display is not None
    joined = " ".join(display["items"]).lower()
    assert len(joined) <= 400
    if display["items"]:
        assert "проверьте" not in joined, "процедуры не в показе"
