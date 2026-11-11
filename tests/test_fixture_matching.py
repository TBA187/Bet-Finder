"""Fixture matching across odds sources.

One real match must be stored once (so every bookmaker's price lands on the
same fixture) and in the right competition.  The two cases at the top were seen
in the stored data on 2026-11-09.  The rule stays: a missing merge is better
than a wrong one.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import Fixture, utcnow
from app.core.names import find_fixture, normalise_team, team_similarity
from app.providers.common import attach, match_competition
from app.providers.scraper import ScraperProvider
from app.services.repository import fixture_id_for

KO = datetime(2026, 11, 11, 19, 45, tzinfo=timezone.utc)


def _fx(home, away, sport="basketball", kickoff=KO, cid="basketball.euroleague", cname="EuroLeague", fid="x"):
    return Fixture(id=fid, sport=sport, competition_id=cid, competition_name=cname, home=home, away=away, kickoff=kickoff)


def _kambi(home, away, start, path, eid=7):
    return {"event": {"id": eid, "homeName": home, "awayName": away, "start": start, "state": "NOT_STARTED",
                      "group": path[-1], "path": [{"englishName": p} for p in path]},
            "betOffers": [{"betOfferType": {"englishName": "Match"}, "criterion": {"englishLabel": "Full Time"},
                           "outcomes": [{"type": "OT_ONE", "odds": 2100}, {"type": "OT_CROSS", "odds": 3400},
                                        {"type": "OT_TWO", "odds": 3500}]}]}


# ------------------------------------------------- case 1: sponsor / city suffix
def test_sponsor_or_city_suffix_is_one_fixture_when_opponent_and_kickoff_agree():
    stored = _fx("KK Partizan NIS", "Saski Baskonia")
    hit = find_fixture("basketball", "Partizan Belgrade", "Saski Baskonia", KO, [stored])
    assert hit and hit[0].id == "x" and hit[2] is False
    # the other way round (scraper stored it first), and with the sides swapped
    stored = _fx("Partizan Belgrade", "Saski Baskonia")
    assert find_fixture("basketball", "KK Partizan NIS", "Saski Baskonia", KO, [stored])
    swapped = find_fixture("basketball", "Saski Baskonia", "KK Partizan NIS", KO, [stored])
    assert swapped and swapped[2] is True


def test_the_names_alone_are_still_not_a_match():
    assert team_similarity("Partizan Belgrade", "KK Partizan NIS") < 0.78


def test_weak_side_needs_the_same_kickoff():
    stored = _fx("KK Partizan NIS", "Saski Baskonia")
    assert find_fixture("basketball", "Partizan Belgrade", "Saski Baskonia", KO + timedelta(minutes=30), [stored]) is None
    assert find_fixture("basketball", "Partizan Belgrade", "Saski Baskonia", None, [stored]) is None


def test_weak_side_needs_an_exact_opponent():
    stored = _fx("KK Partizan NIS", "Saski Baskonia")
    # "Baskonia" alone is a good match but not an exact one: two uncertain sides are not merged
    assert find_fixture("basketball", "Partizan Belgrade", "Baskonia Vitoria", KO, [stored]) is None
    # both sides only share a city or a generic word: different matches at the same time
    other = _fx("Manchester United", "Real Sociedad", sport="football", cid="football.uel", cname="UEFA Europa League")
    assert find_fixture("football", "Manchester City", "Real Betis", KO, [other]) is None


def test_weak_side_needs_a_distinctive_shared_name():
    stored = _fx("KK Partizan NIS", "Saski Baskonia")
    assert find_fixture("basketball", "Red Star Belgrade", "Saski Baskonia", KO, [stored]) is None
    # not merged on purpose: nothing in the two spellings says they are the same club
    zvezda = _fx("KK Crvena Zvezda", "Saski Baskonia")
    assert find_fixture("basketball", "Red Star Belgrade", "Saski Baskonia", KO, [zvezda]) is None
    # "Real" is shared by many clubs: not enough
    real = _fx("Barcelona", "Real Sociedad", sport="football", cid="football.la_liga", cname="La Liga")
    assert find_fixture("football", "Barcelona", "Real Betis", KO, [real]) is None


def test_weak_side_rule_is_for_team_sports_only():
    stored = _fx("Qualifier", "Alexander Zverev", sport="tennis", cid="tennis.atp", cname="ATP Tour")
    assert find_fixture("tennis", "Qualifier", "Mischa Zverev", KO, [stored]) is None


def test_a_full_match_wins_over_a_weak_side_match():
    weak = _fx("Partizan Belgrade", "Saski Baskonia", fid="weak")
    full = _fx("KK Partizan NIS", "Saski Baskonia", fid="full")
    for pool in ([weak, full], [full, weak]):
        assert find_fixture("basketball", "KK Partizan NIS", "Saski Baskonia", KO, pool)[0].id == "full"


def test_both_sources_end_up_on_one_fixture(repo, registry):
    # The Odds API first, then the scraper
    api = _fx("KK Partizan NIS", "Saski Baskonia", fid="")
    stored, _ = repo.merge_fixture(api, "the_odds_api", "abc")
    hit = attach(repo, registry, "scraper", "basketball", "1001", "Partizan Belgrade", "Saski Baskonia", KO,
                 ("Euroleague",), repo.fixtures(), create=True, country="")
    assert hit and hit[0].id == stored.id and hit[1] is False
    assert len(repo.fixtures()) == 1 and set(repo.fixtures()[0].refs) == {"the_odds_api", "scraper"}


def test_both_sources_end_up_on_one_fixture_scraper_first(repo, registry):
    hit = attach(repo, registry, "scraper", "basketball", "1001", "Partizan Belgrade", "Saski Baskonia", KO,
                 ("Euroleague",), [], create=True, country="")
    assert hit and hit[0].competition_id == "basketball.euroleague"
    stored, swapped = repo.merge_fixture(_fx("KK Partizan NIS", "Saski Baskonia", fid=""), "the_odds_api", "abc")
    assert stored.id == hit[0].id and swapped is False and len(repo.fixtures()) == 1


# --------------------------------------------------- case 2: women's teams
@pytest.mark.parametrize("women,men", [
    ("1. FC Köln (W)", "1. FC Köln"), ("RB Leipzig (W)", "RB Leipzig"), ("Arsenal Women", "Arsenal"),
    ("FC Bayern München Frauen", "FC Bayern München"), ("Olympique Lyonnais (F)", "Olympique Lyonnais"),
    ("Chelsea FC Women", "Chelsea FC"), ("Chelsea W", "Chelsea"), ("Real Madrid Femenino", "Real Madrid"),
    ("Paris Saint-Germain Féminines", "Paris Saint-Germain"), ("Fortuna Hjørring (K)", "Fortuna Hjørring"),
    ("Arsenal WFC", "Arsenal FC"),
])
def test_womens_team_never_matches_the_mens_team(women, men):
    assert team_similarity(women, men) < 0.78
    assert team_similarity(men, women) < 0.78


@pytest.mark.parametrize("a,b", [
    ("1. FC Köln (W)", "FC Köln Frauen"), ("Arsenal Women", "Arsenal (W)"), ("RB Leipzig (W)", "RB Leipzig Women"),
    ("Olympique Lyonnais (F)", "Lyon Féminin"), ("Chelsea W", "Chelsea FC Women"),
])
def test_same_womens_team_matches_across_spellings(a, b):
    assert team_similarity(a, b) >= 0.78


@pytest.mark.parametrize("youth,senior", [("Arsenal U21", "Arsenal"), ("Denmark U-21", "Denmark"), ("Spain U19", "Spain U21")])
def test_youth_team_never_matches_another_age_group(youth, senior):
    assert team_similarity(youth, senior) < 0.78


def test_same_youth_team_matches():
    assert team_similarity("Denmark U21", "Denmark U-21") >= 0.78


def test_womens_match_is_not_the_mens_fixture():
    mens = _fx("1. FC Köln", "RB Leipzig", sport="football", cid="football.bundesliga", cname="Bundesliga")
    assert find_fixture("football", "1. FC Köln (W)", "RB Leipzig (W)", KO, [mens]) is None
    assert find_fixture("football", "1. FC Köln Frauen", "RB Leipzig", KO, [mens]) is None
    # the league says "women" even when the team names do not
    assert find_fixture("football", "1. FC Köln", "RB Leipzig", KO, [mens], leagues=("Bundesliga (W)",)) is None
    womens = _fx("1. FC Köln (W)", "RB Leipzig (W)", sport="football", cid="football.bundesliga", cname="Bundesliga")
    assert find_fixture("football", "1. FC Köln", "RB Leipzig", KO, [womens]) is None
    assert find_fixture("football", "1. FC Köln", "RB Leipzig", KO, [womens], leagues=("Bundesliga",)) is None


def test_womens_match_is_found_whichever_side_carries_the_marker():
    # The Odds API lists a women's league without marking the team names
    stored = _fx("1. FC Köln", "RB Leipzig", sport="football", cid="football.bundesliga_women", cname="Frauen-Bundesliga")
    assert find_fixture("football", "1. FC Köln (W)", "RB Leipzig (W)", KO, [stored])
    assert find_fixture("football", "FC Köln", "RB Leipzig", KO, [stored], leagues=("Bundesliga (W)",))
    assert find_fixture("football", "FC Köln", "RB Leipzig", KO, [stored], leagues=("Bundesliga",)) is None
    # a source that sends no league at all cannot be judged: matched on the names, as before
    assert find_fixture("football", "FC Köln", "RB Leipzig", KO, [stored])
    marked = _fx("1. FC Köln (W)", "RB Leipzig (W)", sport="football", cid="football.bundesliga_women", cname="Frauen-Bundesliga")
    assert find_fixture("football", "FC Köln Frauen", "RB Leipzig Women", KO, [marked])
    assert find_fixture("football", "FC Köln", "RB Leipzig", KO, [marked], leagues=("Frauen-Bundesliga",))


def test_womens_league_never_resolves_to_a_mens_competition(registry):
    assert match_competition(registry, "football", "Bundesliga", country="Germany")["id"] == "football.bundesliga"
    for name in ("Bundesliga (W)", "Bundesliga Women", "Frauen-Bundesliga", "Bundesliga (F)", "Bundesliga W"):
        assert match_competition(registry, "football", name, country="Germany") is None, name  # women's league is off
    assert match_competition(registry, "football", "Premier League (W)", country="England") is None
    assert match_competition(registry, "football", "UEFA Champions League (W)") is None
    assert match_competition(registry, "basketball", "Euroleague (W)") is None
    registry.set_enabled("football.bundesliga_women", True)
    for name in ("Bundesliga (W)", "Bundesliga Women", "Frauen-Bundesliga"):
        assert match_competition(registry, "football", name, country="Germany")["id"] == "football.bundesliga_women", name
    assert match_competition(registry, "football", "Bundesliga", country="Germany")["id"] == "football.bundesliga"


def test_another_division_never_resolves_to_the_top_division(registry):
    # "2. Bundesliga" is one character away from "Bundesliga" - and a different league
    assert match_competition(registry, "football", "2. Bundesliga", country="Germany") is None
    assert match_competition(registry, "football", "Premier League 2", country="England") is None
    registry.set_enabled("football.bundesliga2", True)
    assert match_competition(registry, "football", "2. Bundesliga", country="Germany")["id"] == "football.bundesliga2"
    assert match_competition(registry, "football", "Bundesliga", country="Germany")["id"] == "football.bundesliga"
    registry.set_enabled("football.k_league", True)
    assert match_competition(registry, "football", "K League 2", country="South Korea") is None
    assert match_competition(registry, "football", "K League 1", country="South Korea")["id"] == "football.k_league"


def test_attach_never_puts_a_womens_match_on_a_mens_fixture_or_competition(repo, registry):
    mens = _fx("1. FC Köln", "RB Leipzig", sport="football", cid="football.bundesliga", cname="Bundesliga", fid="mens")
    repo.save_fixture(mens)
    for leagues in (("Bundesliga (W)",), ("Bundesliga",), ()):
        assert attach(repo, registry, "scraper", "football", "55", "1. FC Köln (W)", "RB Leipzig (W)", KO, leagues,
                      [mens], create=True, country="Germany") is None
    # unmarked team names, the league path says women
    assert attach(repo, registry, "scraper", "football", "55", "1. FC Köln", "RB Leipzig", KO, ("Bundesliga",),
                  [mens], create=True, country="Germany", context=("Football", "Germany", "Women")) is None
    assert [f.id for f in repo.fixtures()] == ["mens"] and "scraper" not in repo.fixtures()[0].refs
    # the men's match from the same source still lands on the men's fixture
    hit = attach(repo, registry, "scraper", "football", "56", "FC Köln", "RB Leipzig", KO, ("Bundesliga",),
                 [mens], create=True, country="Germany")
    assert hit and hit[0].id == "mens"


def test_scraper_does_not_file_a_womens_match_under_the_mens_league(repo, registry, settings):
    prov = ScraperProvider(repo, registry, settings)
    now = utcnow()
    start = (now + timedelta(hours=5)).isoformat()
    for path in (["Football", "Germany", "Bundesliga (W)"], ["Football", "Germany", "Bundesliga"],
                 ["Football", "Women", "Bundesliga"]):
        home, away = ("1. FC Köln", "RB Leipzig") if "Women" in path else ("1. FC Köln (W)", "RB Leipzig (W)")
        assert prov._kambi_event(_kambi(home, away, start, path), "football", "unibet", [], now) == 0, path
    assert repo.fixtures() == []
    # with the women's league switched on it is stored there - and only there
    registry.set_enabled("football.bundesliga_women", True)
    assert prov._kambi_event(_kambi("1. FC Köln (W)", "RB Leipzig (W)", start, ["Football", "Germany", "Bundesliga (W)"]),
                             "football", "unibet", [], now) == 3
    assert [f.competition_id for f in repo.fixtures()] == ["football.bundesliga_women"]
    # the men's match at the same time is a second fixture
    assert prov._kambi_event(_kambi("1. FC Köln", "RB Leipzig", start, ["Football", "Germany", "Bundesliga"], eid=8),
                             "football", "unibet", repo.fixtures(), now) == 3
    assert sorted(f.competition_id for f in repo.fixtures()) == ["football.bundesliga", "football.bundesliga_women"]


def test_womens_fixture_merges_across_sources(repo, registry):
    registry.set_enabled("football.bundesliga_women", True)
    api = _fx("1. FC Köln", "RB Leipzig", sport="football", cid="football.bundesliga_women", cname="Frauen-Bundesliga", fid="")
    stored, _ = repo.merge_fixture(api, "the_odds_api", "w1")
    hit = attach(repo, registry, "scraper", "football", "77", "1. FC Köln (W)", "RB Leipzig (W)", KO, ("Bundesliga (W)",),
                 repo.fixtures(), create=True, country="Germany")
    assert hit and hit[0].id == stored.id and len(repo.fixtures()) == 1
    # the men's match of the same clubs at the same time stays a separate fixture
    mens = _fx("1. FC Köln", "RB Leipzig", sport="football", cid="football.bundesliga", cname="Bundesliga", fid="")
    other, _ = repo.merge_fixture(mens, "the_odds_api", "m1")
    assert other.id != stored.id and len(repo.fixtures()) == 2
    assert {f.competition_id: sorted(f.refs) for f in repo.fixtures()} == {
        "football.bundesliga_women": ["scraper", "the_odds_api"], "football.bundesliga": ["the_odds_api"]}


# ------------------------------------------------------- sport-aware aliases
def test_football_nicknames_are_not_applied_to_other_sports():
    assert normalise_team("Spurs", "football") == "tottenham"
    assert normalise_team("San Antonio Spurs", "basketball") == "san antonio spurs"
    assert normalise_team("Chicago Wolves", "icehockey") == "chicago wolves"
    assert team_similarity("Spurs", "Tottenham Hotspur", "football") >= 0.78
    assert team_similarity("Wolves", "Wolverhampton Wanderers", "football") >= 0.78
    assert team_similarity("San Antonio Spurs", "Tottenham", "basketball") < 0.78
    # aliases that hold in every sport still work everywhere
    assert team_similarity("LA Lakers", "Los Angeles Lakers", "basketball") >= 0.78
    assert team_similarity("FC Bayern München", "Bayern Munich", "basketball") >= 0.78
    # sport unknown (free-text search): every alias applies, as before
    assert normalise_team("Spurs") == "tottenham"


def test_fixture_id_keeps_the_real_team_name():
    assert "san-antonio-spurs" in fixture_id_for("basketball", "Phoenix Suns", "San Antonio Spurs", KO)
    assert "tottenham" in fixture_id_for("football", "Spurs", "Arsenal", KO)
