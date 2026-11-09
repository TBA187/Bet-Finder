"""Stored entities.  Validated with pydantic whenever they are loaded from JSON,
so a malformed record is skipped instead of crashing the app."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.markets import MARKET_TYPES, normalise_line
from app.core.odds_math import validate_odds
from app.core.sports import is_sport

FixtureStatus = Literal["scheduled", "live", "finished", "postponed", "cancelled", "unknown"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Fixture(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    sport: str
    competition_id: str
    competition_name: str
    home: str
    away: str
    kickoff: datetime
    status: FixtureStatus = "scheduled"
    score_home: Optional[int] = None
    score_away: Optional[int] = None
    # provider -> provider's event id
    refs: dict[str, str] = Field(default_factory=dict)
    # provider -> {"home": team id, "away": team id}
    team_refs: dict[str, dict[str, str]] = Field(default_factory=dict)
    # provider -> {"league": id, "season": season}
    league_refs: dict[str, dict[str, str]] = Field(default_factory=dict)
    is_demo: bool = False
    manual: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("kickoff", "created_at", "updated_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return as_utc(v)

    @field_validator("sport")
    @classmethod
    def _sport(cls, v: str) -> str:
        if not is_sport(v):
            raise ValueError(f"unsupported sport {v}")
        return v

    @property
    def name(self) -> str:
        return f"{self.home} vs {self.away}"


class Quote(BaseModel):
    """The current price of one selection at one bookmaker (+ its history)."""
    model_config = ConfigDict(extra="ignore")

    fixture_id: str
    market: str
    line: Optional[float] = None
    selection: str
    bookmaker: str
    odds: float
    source: str  # the_odds_api | api_sports | manual | demo
    fetched_at: datetime
    book_updated_at: Optional[datetime] = None
    # [[iso timestamp, odds], ...] oldest first; only changes are recorded
    history: list[tuple[datetime, float]] = Field(default_factory=list)

    @field_validator("market")
    @classmethod
    def _market(cls, v: str) -> str:
        if v not in MARKET_TYPES:
            raise ValueError(f"unknown market {v}")
        return v

    @field_validator("line", mode="before")
    @classmethod
    def _line(cls, v):
        return normalise_line(v)

    @field_validator("odds")
    @classmethod
    def _odds(cls, v: float) -> float:
        return round(validate_odds(v), 3)

    @field_validator("fetched_at", "book_updated_at")
    @classmethod
    def _utc(cls, v):
        return as_utc(v) if v is not None else v

    @property
    def key(self) -> str:
        line = "" if self.line is None else f"{self.line:g}"
        return f"{self.source}|{self.market}|{line}|{self.selection}|{self.bookmaker}"

    @property
    def market_id(self) -> str:
        from app.core.markets import market_id
        return market_id(self.market, self.line)


class Absence(BaseModel):
    """A missing/doubtful player (from a provider or entered manually)."""
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    team: Literal["home", "away"]
    player: str
    kind: Literal["injury", "suspension", "doubtful", "other"] = "injury"
    importance: Literal["key", "regular", "rotation", "unknown"] = "unknown"
    reason: str = ""
    source: str = "manual"
    added_at: datetime = Field(default_factory=utcnow)


class LineupInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["confirmed", "expected", "unknown"] = "unknown"
    formation: str = ""
    players: list[str] = Field(default_factory=list)
    rotated: Optional[bool] = None  # manual flag: team is resting players
    changes_from_last: Optional[int] = None
    source: str = "manual"
    updated_at: datetime = Field(default_factory=utcnow)


class TeamStats(BaseModel):
    """Normalised team statistics for one competition/season.

    ``scored``/``conceded`` are per-game averages of goals (or points)."""
    model_config = ConfigDict(extra="ignore")

    team: str
    source: str
    season: str = ""
    games_home: int = 0
    games_away: int = 0
    scored_home: Optional[float] = None
    conceded_home: Optional[float] = None
    scored_away: Optional[float] = None
    conceded_away: Optional[float] = None
    wins_home: Optional[int] = None
    draws_home: Optional[int] = None
    losses_home: Optional[int] = None
    wins_away: Optional[int] = None
    draws_away: Optional[int] = None
    losses_away: Optional[int] = None
    clean_sheets: Optional[int] = None
    failed_to_score: Optional[int] = None
    # expected goals per game (football), from provider match statistics or manual entry
    xg_for: Optional[float] = None
    xg_against: Optional[float] = None
    xg_games: int = 0
    xg_source: str = ""
    # Most recent LAST: [{"date", "venue": "H"/"A", "opponent", "scored", "conceded"}]
    recent: list[dict] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def games(self) -> int:
        return self.games_home + self.games_away

    def overall(self, field: str) -> Optional[float]:
        h, a = getattr(self, f"{field}_home"), getattr(self, f"{field}_away")
        if h is None or a is None or self.games == 0:
            return h if a is None else a
        return (h * self.games_home + a * self.games_away) / self.games
