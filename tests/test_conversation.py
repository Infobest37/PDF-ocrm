def test_conversation_records_user_and_assistant_messages_in_order():
    from pdfgraph.conversation import Conversation

    conversation = Conversation()
    citation = {
        "document": "manual.pdf",
        "page": 1,
        "snippet": "Проверенная выдержка",
    }

    conversation.add_user("Вопрос 1")
    conversation.add_assistant(
        "Ответ 1", citations=[citation], kind="answer",
    )
    conversation.add_user("Вопрос 2")
    conversation.add_assistant("Выберите вариант", kind="clarify")

    assert [(message.role, message.text, message.kind)
            for message in conversation.history] == [
        ("user", "Вопрос 1", None),
        ("assistant", "Ответ 1", "answer"),
        ("user", "Вопрос 2", None),
        ("assistant", "Выберите вариант", "clarify"),
    ]
    assert conversation.history[1].citations == [citation]
    assert conversation.history[0].citations == []


def test_conversation_resolves_choice_and_enriches_query():
    from pdfgraph.conversation import Conversation
    from pdfgraph.query import Fragment

    fragment = Fragment(
        document="manual.pdf", page=12, system_code="023",
        subsystem_code="023.11", snippet="Втулка привода",
        char_start=0, char_end=14, score=0.8, kind="text",
    )
    conversation = Conversation()
    conversation.begin_question("как заменить деталь")
    conversation.add_clarification(
        "Выберите вариант", options=[{
        "id": "A", "label": "Втулка привода",
            "fragment_ids": [0], "query_terms": [],
        }], fragments=[fragment],
    )

    result = conversation.resolve_clarification_reply("a")

    assert result["action"] == "select"
    assert result["fragments"] == [fragment]
    assert result["query"] == "как заменить деталь Втулка привода"
    assert conversation.pending_options == []


def test_conversation_refines_query_without_excluding_original_terms():
    from pdfgraph.conversation import Conversation
    from pdfgraph.query import Fragment

    fragment = Fragment(
        document="manual.pdf", page=12, system_code="023",
        subsystem_code="023.11", snippet="Втулка привода",
        char_start=0, char_end=14, score=0.8, kind="text",
    )
    conversation = Conversation()
    conversation.begin_question("как заменить деталь")
    conversation.add_clarification(
        "Выберите вариант", options=[{"id": "A", "label": "A",
                                      "fragment_ids": [0]}],
        fragments=[fragment],
    )

    result = conversation.resolve_clarification_reply("Не то: механизм привода")

    assert result == {
        "action": "refine",
        "query": "как заменить деталь механизм привода",
    }
    assert conversation.pending_options == []


def test_conversation_choice_enriches_query_when_label_is_already_present():
    from pdfgraph.conversation import Conversation
    from pdfgraph.query import Fragment

    fragment = Fragment(
        document="manual.pdf", page=12, system_code="023.00.00",
        subsystem_code="023.11.30", snippet="Втулка привода",
        char_start=0, char_end=14, score=0.8, kind="text",
    )
    conversation = Conversation()
    conversation.begin_question("радиомаяк")
    conversation.add_clarification(
        "Выберите вариант", options=[{
            "id": "A", "label": "радиомаяк", "fragment_ids": [0],
        }], fragments=[fragment],
    )

    result = conversation.resolve_clarification_reply("A")

    assert result["query"] == "радиомаяк 023.11.30"


def test_conversation_caps_clarifications_and_resets_for_new_question():
    from pdfgraph.conversation import Conversation
    from pdfgraph.query import Fragment

    fragment = Fragment(
        document="manual.pdf", page=12, system_code="023",
        subsystem_code="023.11", snippet="Втулка привода",
        char_start=0, char_end=14, score=0.8, kind="text",
    )
    options = [{"id": "A", "label": "A", "fragment_ids": [0]}]
    conversation = Conversation(max_clarification_rounds=2)
    conversation.begin_question("вопрос")
    assert conversation.add_clarification(
        "Выберите", options=options, fragments=[fragment]) is not None
    conversation.resolve_clarification_reply("A")
    assert conversation.add_clarification(
        "Уточните", options=options, fragments=[fragment]) is not None
    conversation.resolve_clarification_reply("Не то: уточнение")
    assert conversation.add_clarification(
        "Ещё уточнение", options=options, fragments=[fragment]) is None
    assert conversation.clarification_rounds == 2

    conversation.begin_question("новый вопрос")
    assert conversation.can_clarify
    assert conversation.current_query == "новый вопрос"
