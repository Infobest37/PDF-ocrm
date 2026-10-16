from dataclasses import dataclass, field

#: Version of the structural-data schema; bumped when the dataclass shapes
#: below change (fields added/removed/renamed).
SCHEMA_VERSION: int = 2


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float


@dataclass
class Table:
    page_index: int
    bbox: tuple[float, float, float, float]
    rows: list[list[str]]
    ambiguous: list[list[bool]] = field(default_factory=list)


@dataclass
class Figure:
    page_index: int
    number: str
    caption: str


@dataclass
class Reference:
    page_index: int
    kind: str
    target: str
    raw: str


@dataclass
class PageInfo:
    page_index: int
    is_native_text: bool
    is_blank: bool
    system_code: str
    subsystem_code: str
    full_text: str
    words: list[Word]
    tables: list[Table] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)


@dataclass
class DocResult:
    source_path: str
    page_count: int
    pages: list[PageInfo]
    system_names: dict[str, str]
    schema_version: int = SCHEMA_VERSION


@dataclass
class QualityFlag:
    page_index: int
    reason: str
    avg_conf: float
    text_coverage: float
