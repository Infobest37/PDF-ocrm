"""In-memory conversation messages for the PDFGraph chat UI."""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias


MessageRole: TypeAlias = Literal["user", "assistant"]
MessageKind: TypeAlias = Literal["answer", "refusal", "clarify"]
Citation: TypeAlias = dict[str, str | int]


@dataclass
class Message:
    """One user or assistant message with optional verified citations."""

    role: MessageRole
    text: str
    citations: list[Citation] = field(default_factory=list)
    kind: MessageKind | None = None


@dataclass
class Conversation:
    """Ordered, session-local history of user and assistant messages."""

    history: list[Message] = field(default_factory=list)

    def add_user(self, text: str) -> Message:
        """Append and return a user message."""
        message = Message(role="user", text=text)
        self.history.append(message)
        return message

    def add_assistant(self, text: str, citations: list[Citation] | None = None,
                      kind: MessageKind = "answer") -> Message:
        """Append and return an assistant answer, refusal, or clarification."""
        message = Message(
            role="assistant",
            text=text,
            citations=list(citations) if citations is not None else [],
            kind=kind,
        )
        self.history.append(message)
        return message
