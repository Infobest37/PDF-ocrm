"""Persist provider credentials and answer preferences with Qt settings."""

from dataclasses import dataclass

from PyQt5.QtCore import QSettings

DEFAULT_BASE_URL = "https://aitunnel.ru/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def _as_bool(value) -> bool:  # type: ignore[no-untyped-def]
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass
class LLMSettings:
    """Saved aitunnel profile and the two opt-in GUI search preferences."""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    max_tokens: int = 500
    use_synthesis: bool = False
    use_rag_fusion: bool = False

    @classmethod
    def load(cls) -> "LLMSettings":
        return load()

    def save(self) -> None:
        save(self)


def load() -> LLMSettings:
    """Load all LLM and retrieval preferences from the per-user QSettings."""
    settings = QSettings("PDFGraph", "LLM")
    return LLMSettings(
        api_key=str(settings.value("api_key", "") or ""),
        base_url=str(settings.value("base_url", DEFAULT_BASE_URL)
                     or DEFAULT_BASE_URL),
        model=str(settings.value("model", DEFAULT_MODEL) or DEFAULT_MODEL),
        temperature=float(settings.value("temperature", 0.0)),
        max_tokens=int(settings.value("max_tokens", 500)),
        use_synthesis=_as_bool(settings.value("use_synthesis", False)),
        use_rag_fusion=_as_bool(settings.value("use_rag_fusion", False)),
    )


def save(settings: LLMSettings) -> None:
    """Persist profile and opt-in switches in the current user's settings."""
    store = QSettings("PDFGraph", "LLM")
    store.setValue("api_key", settings.api_key)
    store.setValue("base_url", settings.base_url)
    store.setValue("model", settings.model)
    store.setValue("temperature", float(settings.temperature))
    store.setValue("max_tokens", int(settings.max_tokens))
    store.setValue("use_synthesis", bool(settings.use_synthesis))
    store.setValue("use_rag_fusion", bool(settings.use_rag_fusion))
    store.sync()
