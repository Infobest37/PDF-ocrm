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
