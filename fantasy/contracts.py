from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol


SCHEMA_VERSION = 1

Scoring = Literal["STD", "HALF", "PPR"]
RankingKind = Literal["ecr", "adp", "editorial"]
RankingScope = Literal["overall", "position"]
TrendType = Literal["add", "drop"]
NewsVerdict = Literal["positive", "neutral", "negative", "mixed", "uncertain"]
NewsConfidence = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class Team:
    """A stable NFL-team record, independent of any provider's team object."""

    id: str
    name: str | None = None
    bye_week: int | None = None


@dataclass(frozen=True, slots=True)
class Ranking:
    """One provider's opinion about one player.

    Keeping these as observations instead of flattening ``ecr``/``adp`` onto a
    player lets the UI compare sources without inventing source-specific fields.
    """

    source: str
    kind: RankingKind
    rank: float
    scope: RankingScope = "overall"
    scoring: Scoring | None = None
    position: str | None = None
    tier: int | str | None = None
    best: float | None = None
    worst: float | None = None
    average: float | None = None
    as_of: str | None = None

    @property
    def key(self) -> str:
        bits = (self.source, self.kind, self.scope, self.position or "ALL", self.scoring or "ANY")
        return ":".join(bits)


@dataclass(frozen=True, slots=True)
class Projection:
    """A provider projection; ``stats`` remains extensible across positions."""

    source: str
    season: int
    scoring: Scoring
    points: float | None = None
    week: int | None = None
    stats: Mapping[str, float] = field(default_factory=dict)
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class Trend:
    source: str
    type: TrendType
    count: int
    lookback_hours: int
    rank: int | None = None
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class Player:
    """Canonical player identity plus normalized provider observations."""

    id: str
    name: str
    positions: tuple[str, ...]
    team_id: str | None = None
    active: bool | None = None
    injury_status: str | None = None
    source_ids: Mapping[str, str] = field(default_factory=dict)
    rankings: tuple[Ranking, ...] = ()
    projections: tuple[Projection, ...] = ()
    trends: tuple[Trend, ...] = ()

    def ranking(self, source: str, kind: RankingKind) -> Ranking | None:
        return next((item for item in self.rankings if item.source == source and item.kind == kind), None)


@dataclass(frozen=True, slots=True)
class DraftPick:
    pick_no: int
    round: int
    draft_slot: int
    player_id: str | None
    roster_id: int | None = None
    is_keeper: bool = False


@dataclass(frozen=True, slots=True)
class DraftState:
    id: str
    status: str
    current_pick: int | None
    user_roster_id: int
    user_draft_slot: int
    picks: tuple[DraftPick, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceStatus:
    source: str
    dataset: str
    fetched_at: str
    row_count: int


@dataclass(frozen=True, slots=True)
class DraftProposal:
    """One assistant-authored choice set displayed as a board row."""

    share: int
    players: tuple[str, ...]
    reason: str = ""


CouncilConfidence = Literal["low", "medium", "high"]
CouncilTurnKind = Literal["single", "pair", "complement"]


@dataclass(frozen=True, slots=True)
class CouncilConstruction:
    """One ranked construction from a council take or the merged summary."""

    players: tuple[str, ...]
    lean: int
    why: str = ""
    support: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CouncilAvoid:
    player: str
    why: str = ""


@dataclass(frozen=True, slots=True)
class CouncilTake:
    """One model's independent writeback. Never written to advice.json."""

    model: str
    based_on_pick: int
    turn: tuple[int, ...]
    constructions: tuple[CouncilConstruction, ...]
    avoid: tuple[CouncilAvoid, ...] = ()
    confidence: CouncilConfidence = "medium"
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdviceMessage:
    """Assistant-to-TUI transmission, invalidated when the draft advances."""

    published_at: str
    headline: str
    body: str = ""
    based_on_pick: int | None = None
    players: tuple[str, ...] = ()
    proposals: tuple[DraftProposal, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NewsFlag:
    severity: Literal["info", "watch", "material"]
    type: str
    claim: str


@dataclass(frozen=True, slots=True)
class NewsSource:
    title: str
    url: str
    published_at: str
    supports: str = ""


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    published_at: str | None = None
    publisher: str = ""
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class NewsReview:
    outcome: Literal["pass", "pass_with_caveats", "fail"]
    concerns: tuple[str, ...] = ()
    role: str = "fast-sanity"


@dataclass(frozen=True, slots=True)
class NewsCheck:
    """One cacheable, lazily requested TinyFish fantasy outlook."""

    player_id: str
    subject: str
    query: str
    query_fingerprint: str
    checked_at: str
    expires_at: str
    verdict: NewsVerdict
    confidence: NewsConfidence
    summary: str
    team: str | None = None
    provider: str = "tinyfish"
    purpose: str = "Assess current fantasy-football draft outlook"
    recency_minutes: int = 10080
    summary_author: str = "fast-agent"
    search_results: tuple[SearchResult, ...] = ()
    flags: tuple[NewsFlag, ...] = ()
    sources: tuple[NewsSource, ...] = ()
    reviewer: NewsReview = NewsReview("pass")
    window_days: int = 7
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The versioned wire object consumed by a terminal UI."""

    generated_at: str
    scoring: Scoring
    league_id: str
    draft: DraftState
    teams: tuple[Team, ...]
    players: tuple[Player, ...]
    sources: tuple[SourceStatus, ...]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RankingPreference:
    source: str
    kind: RankingKind


@dataclass(frozen=True, slots=True)
class BoardQuery:
    positions: tuple[str, ...] = ()
    available_only: bool = True
    ranking_preferences: tuple[RankingPreference, ...] = (
        RankingPreference("fantasypros", "ecr"),
        RankingPreference("flock", "editorial"),
        RankingPreference("fantasypros", "adp"),
    )
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class BoardRow:
    player: Player
    available: bool
    selected_at: int | None = None


class FantasyData(Protocol):
    """Stable application boundary; provider adapters live behind this API."""

    def snapshot(self) -> Snapshot: ...

    def board(self, query: BoardQuery = BoardQuery()) -> tuple[BoardRow, ...]: ...

    def player(self, player_id: str) -> Player | None: ...
