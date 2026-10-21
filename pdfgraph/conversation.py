"""In-memory conversation messages for the PDFGraph chat UI."""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from pdfgraph.query import Fragment


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
    max_clarification_rounds: int = 2
    clarification_rounds: int = 0
    pending_question: str | None = None
    current_query: str = ""
    pending_options: list[dict] = field(default_factory=list)
    pending_fragments: list[Fragment] = field(default_factory=list)

    @property
    def can_clarify(self) -> bool:
        """Whether the current question may receive another clarification."""
        return self.clarification_rounds < self.max_clarification_rounds

    def add_user(self, text: str) -> Message:
        """Append and return a user message."""
        message = Message(role="user", text=text)
        self.history.append(message)
        return message

    def begin_question(self, text: str) -> Message:
        """Start an independent question and clear prior clarification state."""
        self.reset_clarification_rounds()
        self.pending_question = text
        self.current_query = text
        return self.add_user(text)

    def add_assistant(self, text: str, citations: list[Citation] | None = None,
                      kind: MessageKind = "answer") -> Message:
        """Append and return an assistant answer, refusal, or clarification."""
        if kind != "clarify":
            self.reset_clarification_rounds()
        message = Message(
            role="assistant",
            text=text,
            citations=list(citations) if citations is not None else [],
            kind=kind,
        )
        self.history.append(message)
        return message

    def add_clarification(self, text: str,
                          citations: list[Citation] | None = None, *,
                          options: list[dict] | None = None,
                          fragments: list[Fragment] | None = None
                          ) -> Message | None:
        """Append a clarification unless the per-question limit is exhausted."""
        if not self.can_clarify:
            return None
        self.pending_options = list(options or [])
        self.pending_fragments = list(fragments or [])[:10]
        self.clarification_rounds += 1
        return self.add_assistant(text, citations=citations, kind="clarify")

    def resolve_clarification_reply(self, text: str) -> dict | None:
        """Resolve an option ID, or enrich the query from «Не то: ...»."""
        if not self.pending_options:
            return None

        reply = text.strip()
        if reply.casefold().startswith("не то:"):
            correction = reply.split(":", 1)[1].strip()
            if not correction:
                return None
            self.current_query = _append_query_terms(
                self.current_query or self.pending_question or "",
                [correction],
            )
            self.pending_options = []
            self.pending_fragments = []
            return {"action": "refine", "query": self.current_query}

        option = next(
            (item for item in self.pending_options
             if str(item.get("id", "")).casefold() == reply.casefold()),
            None,
        )
        if option is None:
            return None
        selected = [
            self.pending_fragments[index]
            for index in option.get("fragment_ids", [])
            if type(index) is int and 0 <= index < len(self.pending_fragments)
        ]
        if not selected:
            return None
        query_terms = option.get("query_terms", [])
        if not isinstance(query_terms, list) or not query_terms:
            query_terms = [option.get("label", "")]
        base_query = self.current_query or self.pending_question or ""
        self.current_query = _append_query_terms(base_query, query_terms)
        if self.current_query.casefold() == " ".join(base_query.split()).casefold():
            fragment = selected[0]
            evidence_term = (
                fragment.subsystem_code or fragment.system_code
                or fragment.snippet or f"лист {fragment.page}"
            )
            self.current_query = f"{base_query.rstrip()} {evidence_term}".strip()
        self.pending_options = []
        self.pending_fragments = []
        return {
            "action": "select", "option": option,
            "fragments": selected, "query": self.current_query,
        }

    def reset_clarification_rounds(self) -> None:
        """Reset the counter when a new question or final answer starts."""
        self.clarification_rounds = 0
        self.pending_question = None
        self.current_query = ""
        self.pending_options = []
        self.pending_fragments = []


def _append_query_terms(query: str, terms: list[str]) -> str:
    """Add accepted or corrected terms without removing the original query."""
    result = " ".join(query.split())
    for term in terms:
        if not isinstance(term, str):
            continue
        addition = " ".join(term.split())
        if addition and addition.casefold() not in result.casefold():
            result = f"{result} {addition}".strip()
    return result
