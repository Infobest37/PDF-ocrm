"""Тесты канала норм (карточка Ф1.24).

Контракты, закреплённые здесь (писались ДО реализации — ожидался RED):

- ``pdfgraph.norms.is_value_question``: вопрос-значение распознаётся
  (маркеры «какое/каков/сколько/минимальное/максимальное/в каких
  пределах»), процедурное намерение («как заменить», «порядок
  регулировки», «проверка») — нет.
- ``pdfgraph.norms.norm_candidates``: кандидаты через prefix-AND по
  основам параметра и объекта; лист засчитывается только если есть
  окно с (а) объектом, (б) числом с единицей (включая OCR-варианты
  «кгс/см?»), (в) нормативной конструкцией («не менее», «не более»,
  «допускается», «в пределах», диапазон, «±»); окно с процедурным
  маркером («отвернуть», «проверьте») отбрасывается; маркер в другом
  месте страницы не мешает.
- Параметр встречается много раз: лист один раз в канале, скор растёт
  за каждое дополнительное валидное окно (кап 1.5).
- Вес канала 2.0 — параметр ``ranking_weights`` в :func:`rrf_fuse`
  (default None → все существующие вызовы байт-в-байт), НЕ множитель
  к ``Fragment.score``.
- Выключатель ``PDFGRAPH_NORMS``: по умолчанию выключен; ``fuse_hybrid``
  с флагом выкл — порядок байт-в-байт прежний.
"""

from __future__ import annotations

import pytest

from pdfgraph.query import Fragment
from pdfgraph.store import Store
from pdfprepare.normalize import normalize_for_search

FX_DOC = "РТЭ кн.1.pdf"
FX_DOC2 = "ВСУ РЭ.pdf"
FX_AIRCRAFT = "Ми-8МТВ"

#: Лист с нормами (аналог листа 38): два вхождения давления с единицей
#: и «не менее»/диапазоном с «±», расход, время — всё в нормативных
#: конструкциях.
FX_NORMS = ("Давление масла на режиме малого газа не менее 2 кгс/см2. "
            "Давление масла на режиме выше малого газа (3,5±0,5) кгс/см2. "
            "Расход масла не более 0,3 л/ч. "
            "Время выбега ротора не менее 30 с.")
#: Процедурный лист: «давление масла» есть, числа с единицей нет,
#: процедурные маркеры в том же окне.
FX_PROC = ("Проверьте давление масла при работе двигателя. "
           "Отвернуть пробку и замерить давление масла по прибору.")
#: Описательный лист: параметр и объект есть, числа нет.
FX_DESC = "Система давления масла циркулирует и смазывает узлы."
#: Плотный лист: параметр много раз на «длинной» странице (вхождения
#: разнесены заполнителем за пределы окна 240 символов), одно валидное
#: окно с нормой в конце — как на реальном листе в 2700 символов.
_FILLER = "служебный текст заполнения окна без совпадений " * 7
FX_MANY = ("".join(
    f"Давление масла упоминается в пункте. {_FILLER}" for _ in range(9))
    + "Давление масла не менее 4 кгс/см2.")

FX_TEXTS = {38: FX_NORMS, 50: FX_PROC, 60: FX_DESC, 70: FX_MANY}


def _build_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "graph.db"))
    for page, text in FX_TEXTS.items():
        store.upsert_page(FX_DOC, page, FX_AIRCRAFT, "", "", text,
                          normalize_for_search(text))
    return store


@pytest.fixture
def norms_store(tmp_path):
    store = _build_store(tmp_path)
    yield store
    store.close()


# --- is_value_question -------------------------------------------------------


def test_value_question_detected():
    from pdfgraph.norms import is_value_question
    ok, parameter, objects = is_value_question(
        "какое рабочее давление масла в двигателе")
    assert ok is True
    assert parameter == "давление"
    assert any("масл" in obj for obj in objects)


def test_procedural_question_rejected():
    from pdfgraph.norms import is_value_question
    for question in ("порядок регулировки давления масла в системе смазки",
                     "как заменить масло в редукторе ВР-14",
                     "как проверить давление масла"):
        ok, _parameter, _objects = is_value_question(question)
        assert ok is False, question


def test_value_question_kakovo_marker():
    from pdfgraph.norms import is_value_question
    ok, parameter, objects = is_value_question(
        "каков максимальный расход масла двигателя")
    assert ok is True
    assert parameter == "расход"
    assert any("масл" in obj for obj in objects)


# --- norm_candidates ---------------------------------------------------------


def test_norm_candidates_find_norm_sheet(norms_store):
    """Фраза «давление масла» находит лист с числом, единицей и «не менее»."""
    from pdfgraph.norms import norm_candidates
    candidates = norm_candidates(norms_store,
                                 "какое рабочее давление масла в двигателе",
                                 "давление", ["масла"], limit=10)
    keys = [(document, page) for document, page, _score in candidates]
    assert (FX_DOC, 38) in keys
    assert (FX_DOC, 60) not in keys      # описательный: числа нет
    assert (FX_DOC, 50) not in keys      # процедурный: маркер в окне


def test_window_with_ocr_unit_counts(norms_store):
    """OCR-вариант единицы («кгс/см?») в окне засчитывается."""
    store = norms_store
    store.upsert_page(
        FX_DOC2, 10, FX_AIRCRAFT, "", "",
        "Давление масла за насосом не менее 3,5 кгс/см? по прибору.",
        normalize_for_search("Давление масла за насосом не менее "
                             "3,5 кгс/см? по прибору."))
    from pdfgraph.norms import norm_candidates
    candidates = norm_candidates(store,
                                 "какое давление масла за насосом",
                                 "давление", ["насос"], limit=10)
    keys = [(document, page) for document, page, _score in candidates]
    assert (FX_DOC2, 10) in keys


def test_procedural_marker_elsewhere_does_not_block(norms_store):
    """Процедурный маркер в другом месте страницы не мешает окну."""
    from pdfgraph.norms import norm_candidates
    tail = (" Отвернуть пробку следует при обслуживании фильтра." * 8)
    page_text = FX_NORMS + _FILLER + tail
    norms_store.upsert_page(FX_DOC, 90, FX_AIRCRAFT, "", "", page_text,
                            normalize_for_search(page_text))
    candidates = norm_candidates(norms_store,
                                 "какое давление масла в системе",
                                 "давление", ["масла"], limit=20)
    keys = [(document, page) for document, page, _score in candidates]
    assert (FX_DOC, 90) in keys


def test_parameter_many_times_one_slot(norms_store):
    """Параметр 10 раз: лист один раз в канале; скор растёт до капа."""
    from pdfgraph.norms import norm_candidates
    candidates = norm_candidates(norms_store,
                                 "какое давление масла в системе",
                                 "давление", ["масла"], limit=10)
    same = [(document, page, score) for document, page, score
            in candidates if (document, page) == (FX_DOC, 70)]
    assert len(same) == 1
    # Одно валидное окно из десяти вхождений: базовый скор 1.0.
    assert same[0][2] == pytest.approx(1.0, abs=1e-9)


def test_norm_candidates_limit(norms_store):
    from pdfgraph.norms import norm_candidates
    candidates = norm_candidates(norms_store,
                                 "какое давление масла в системе",
                                 "давление", ["масла"], limit=2)
    assert len(candidates) <= 2


# --- вес канала и выключатель ------------------------------------------------


def test_rrf_fuse_ranking_weights():
    """ranking_weights=[1, 2]: второй ранг вкладывается дважды."""
    from pdfgraph.rag_fusion import rrf_fuse
    frag_a = Fragment(document="a.pdf", page=1, system_code="",
                      subsystem_code="", snippet="a", char_start=0,
                      char_end=1, score=0.9, kind="text")
    frag_b = Fragment(document="b.pdf", page=1, system_code="",
                      subsystem_code="", snippet="b", char_start=0,
                      char_end=1, score=0.9, kind="text")
    weighted = rrf_fuse([[frag_a], [frag_b]], k=10,
                        ranking_weights=[1.0, 2.0])
    assert weighted[0] is frag_b          # 2/(10+1) > 1/(10+1)
    plain = rrf_fuse([[frag_a], [frag_b]], k=10)
    assert plain[0] is frag_a             # без весов — прежний порядок


def test_flag_off_fuse_unchanged(norms_store, monkeypatch):
    """Флаг выключен: fuse_hybrid порядок байт-в-байт прежний."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.norms import NORMS_ENV

    monkeypatch.delenv(NORMS_ENV, raising=False)
    frag_a = _frag(FX_DOC, 50, 0.9)
    frag_b = _frag(FX_DOC, 60, 0.8)
    expected = rag_fusion.rrf_fuse([[frag_a], [frag_b]],
                                   k=rag_fusion.effective_fusion_k(),
                                   boost=rag_fusion.effective_exact_boost(),
                                   boost_kinds=rag_fusion.EXACT_KINDS)[:10]
    ordered = rag_fusion.fuse_hybrid([frag_a], [frag_b], limit=10,
                                     store=norms_store,
                                     question="какое давление масла")
    assert ordered == expected


def _frag(document: str, page: int, score: float) -> Fragment:
    text = FX_TEXTS[page]
    return Fragment(document=document, page=page, system_code="",
                    subsystem_code="", snippet=text, char_start=0,
                    char_end=len(text), score=score, kind="text")


def test_flag_on_norms_channel_changes_fusion(norms_store, monkeypatch):
    """Флаг включён: канал норм с весом 2.0 поднимает лист с нормой."""
    import pdfgraph.rag_fusion as rag_fusion
    from pdfgraph.norms import NORMS_ENV

    monkeypatch.setenv(NORMS_ENV, "1")
    # Лидер лексики — процедурный лист 50; канал норм возвращает лист 38.
    lexical = [_frag(FX_DOC, 50, 0.9)]
    ordered = rag_fusion.fuse_hybrid(lexical, [], limit=5,
                                     store=norms_store,
                                     question="какое давление масла")
    assert ordered[0].page == 38


# --- Ф1.31: «минимально допустимая», «равна» и др. — нормативные конструкции --

#: Дословные формулировки реальных листов (РТЭ кн.3 л.64 — карта
#: 021.40.00h; Регламент ч.2 л.141 — приложение 2, перечень записей).
FX_64 = ("Проверьте состояние коллектора и щеток электродвигателя "
         "вентилятора. Замерьте и зафиксируйте высоту щеток. "
         "Минимально допустимая высота щеток равна 13,5 мм. "
         "При необходимости щетки замените новыми из комплекта ЗИП.")
FX_141 = ("№ пп Содержание записи Место записи 3 Высота щеток "
          "электродвигателя МВ-1200: - при измерении высоты щеток + - "
          "- при замене щеток + +")


def _norm_re() -> "re.Pattern[str]":
    from pdfgraph.norms import _NORM_RE
    return _NORM_RE


def test_new_norm_constructions_match():
    """Ф1.31: новые нормативные конструкции распознаются."""
    norm_re = _norm_re()
    for phrase in ("Минимально допустимая высота щеток равна",
                   "максимально допустимое давление",
                   "предельно допустимая температура",
                   "допустимый зазор",
                   "должна быть не менее",
                   "должен быть равен",
                   "давление составляет 4,5",
                   "зазор равен 0,5"):
        assert norm_re.search(phrase), phrase


def test_sheet64_window_valid_after_f131():
    """Ф1.31: на листе 64 («Минимально допустимая высота щеток равна
    13,5 мм») окно признаётся валидным — прежде не признавалось."""
    from pdfgraph.norms import _valid_windows, _stem
    from pdfprepare.normalize import normalize_for_search

    text_norm = normalize_for_search(FX_64)
    valid = _valid_windows(text_norm, "высот", ["щеток"],
                           _stem("высота"))
    assert valid >= 1


def test_sheet141_still_no_norm_window():
    """Ф1.31: на листе 141 (перечень записей, числа с единицей нет)
    валидных окон по-прежнему нет."""
    from pdfgraph.norms import _valid_windows, _stem
    from pdfprepare.normalize import normalize_for_search

    text_norm = normalize_for_search(FX_141)
    valid = _valid_windows(text_norm, "высот", ["щеток"],
                           _stem("высота"))
    assert valid == 0


def test_old_constructions_still_match():
    """Ф1.31: прежние конструкции («не менее», «±», диапазон) не сломаны."""
    norm_re = _norm_re()
    for phrase in ("не менее 2 кгс/см2", "(3,5±0,5) кгс/см2",
                   "от 12 до 16", "в пределах 0,5…1,5", "номинал 27 В",
                   "допускается замена"):
        assert norm_re.search(phrase), phrase
