from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Paths, SettingsService
from app.core.models import Fixture, Quote
from app.main import create_app
from app.services.analysis import AnalysisService
from app.services.registry import Registry
from app.services.repository import Repository
from app.storage.json_store import JsonStore

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(registry=BASE_DIR / "data" / "registry", runtime=tmp_path / "runtime")


@pytest.fixture
def store(paths) -> JsonStore:
    return JsonStore(paths.runtime)


@pytest.fixture
def repo(store) -> Repository:
    return Repository(store)


@pytest.fixture
def registry(paths, store) -> Registry:
    return Registry(paths.registry, store)


@pytest.fixture
def settings(store) -> dict:
    return SettingsService(store).get()


@pytest.fixture
def analysis(repo, registry) -> AnalysisService:
    return AnalysisService(repo, registry)


@pytest.fixture
def client(paths, monkeypatch):
    monkeypatch.delenv("BETFINDER_TOKEN", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    app = create_app(paths, background=False)
    with TestClient(app) as c:
        yield c


def make_fixture(repo: Repository, sport="football", home="Alpha FC", away="Beta United", hours=5, now=NOW,
                 competition_id="football.epl", competition_name="Premier League") -> Fixture:
    fx = Fixture(id=f"t-{sport}-{home[:3]}-{away[:3]}".lower().replace(" ", ""), sport=sport, competition_id=competition_id,
                 competition_name=competition_name, home=home, away=away, kickoff=now + timedelta(hours=hours))
    repo.save_fixture(fx)
    return fx


def add_prices(repo: Repository, fx: Fixture, book: str, prices: dict, market="h2h", line=None, now=NOW, source="the_odds_api"):
    quotes = [Quote(fixture_id=fx.id, market=market, line=line, selection=s, bookmaker=book, odds=o, source=source,
                    fetched_at=now - timedelta(minutes=5)) for s, o in prices.items()]
    repo.upsert_quotes(fx.id, quotes)
