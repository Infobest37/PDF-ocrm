from dataclasses import dataclass


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float


@dataclass
class PageInfo:
    page_index: int
    is_native_text: bool
    is_blank: bool
    system_code: str
    subsystem_code: str
    full_text: str
    words: list[Word]


@dataclass
class DocResult:
    source_path: str
    page_count: int
    pages: list[PageInfo]
    system_names: dict[str, str]


@dataclass
class QualityFlag:
    page_index: int
    reason: str
    avg_conf: float
    text_coverage: float
