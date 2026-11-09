"""Agent framework.

Every agent is an independent analyst.  Phase-2 agents (probability, form,
home/away, injuries, suspensions, lineups, statistics) only see the raw match
context - never each other's output - so they cannot simply agree with one
another.  The engine combines them afterwards and measures how much they
disagree.

An agent states:
    lean_home   -1..+1  evidence that the HOME side is stronger than the
                        market currently prices (negative favours away)
    lean_total  -1..+1  evidence for MORE scoring than the market expects
    confidence   0..1   how much the agent trusts its own view.  Missing
                        data must give low confidence, never invented data.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.core.models import Absence, Fixture, LineupInfo, TeamStats
from app.i18n import lang_of, tr


@dataclass
class AgentReport:
    agent: str
    name: str
    status: str = "no_data"  # ok | partial | no_data | error
    confidence: float = 0.0
    lean_home: float = 0.0
    lean_total: float = 0.0
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(self.confidence, 3)
        d["lean_home"] = round(self.lean_home, 3)
        d["lean_total"] = round(self.lean_total, 3)
        return d


@dataclass
class AnalysisContext:
    fixture: Fixture
    competition: dict
    model: Any  # SportModel
    comparisons: dict[str, dict]
    market_params: Optional[dict]
    market_fit_rms: Optional[float]
    home_stats: Optional[TeamStats]
    away_stats: Optional[TeamStats]
    h2h: Optional[list[dict]]
    absences: list[Absence]
    absences_source: Optional[str]  # None = no injury data source at all
    lineups: dict[str, LineupInfo]
    settings: dict
    now: datetime
    # Filled by the engine before phase-3 agents (value detection, risk) run.
    selections: list[dict] = field(default_factory=list)
    reports: dict[str, AgentReport] = field(default_factory=dict)
    disagreement: Optional[float] = None

    @property
    def sport(self) -> str:
        return self.fixture.sport

    @property
    def lang(self) -> str:
        return lang_of(self.settings)

    def t(self, key: str, **values) -> str:
        return tr(self.lang, key, **values)

    @property
    def hours_to_kickoff(self) -> float:
        return (self.fixture.kickoff - self.now).total_seconds() / 3600.0

    def market_margin(self) -> Optional[float]:
        return self.model.expected_margin(self.market_params) if self.market_params else None

    def market_total(self) -> Optional[float]:
        return self.model.expected_total(self.market_params) if self.market_params else None


class Agent:
    key = "agent"
    name = "Agent"
    phase = 2
    weight = 0.0  # contribution of lean to the combined shift

    def run(self, ctx: AnalysisContext) -> AgentReport:  # pragma: no cover - interface
        raise NotImplementedError

    def report(self, **kwargs) -> AgentReport:
        return AgentReport(agent=self.key, name=self.name, **kwargs)

    def safe_run(self, ctx: AnalysisContext) -> AgentReport:
        try:
            rep = self.run(ctx)
        except Exception as exc:  # one failing agent must never break the analysis
            rep = self.report(status="error", summary=ctx.t("agent_failed", err=f"{type(exc).__name__}: {exc}"),
                              warnings=[ctx.t("agent_failed_warn")])
        rep.name = tr(ctx.lang, f"agent_{self.key}")
        rep.confidence = max(0.0, min(1.0, rep.confidence))
        rep.lean_home = max(-1.0, min(1.0, rep.lean_home))
        rep.lean_total = max(-1.0, min(1.0, rep.lean_total))
        if rep.status in ("no_data", "error"):
            rep.confidence = 0.0
        return rep


def squash(x: float, scale: float = 1.0) -> float:
    """Map any real number into -1..1 smoothly."""
    return math.tanh(x / scale) if scale else 0.0


_MARGIN_SCALE = {"football": 0.6, "icehockey": 0.8, "basketball": 6.0, "handball": 2.5,
                 "americanfootball": 7.0, "baseball": 1.5, "mma": 0.6, "boxing": 0.6, "tennis": 0.6}
_TOTAL_SCALE = {"football": 0.6, "icehockey": 0.8, "basketball": 8.0, "handball": 3.0,
                "americanfootball": 8.0, "baseball": 1.8, "mma": 1.0, "boxing": 1.0, "tennis": 3.0}


def margin_scale(sport: str) -> float:
    """Typical meaningful margin difference per sport (goals/points/log-odds)."""
    return _MARGIN_SCALE.get(sport, 1.0)


def total_scale(sport: str) -> float:
    return _TOTAL_SCALE.get(sport, 1.0)


def result_points(sport: str, scored: float, conceded: float) -> float:
    """Points for a result, scaled 0..1 (win=1; draw=1/3 where draws exist)."""
    from app.core.sports import has_draw
    if scored > conceded:
        return 1.0
    if scored == conceded:
        return 1.0 / 3.0 if has_draw(sport) else 0.5
    return 0.0
