from typing import List
from app.models.match import Match
from app.models.coupon import DailyCoupon

class OddsService:
    def __init__(self):
        self.matches: List[Match] = []
        self.clv_history: List[dict] = []
        self._init_default_data()

    def _init_default_data(self):
        m1 = Match(
            match_id=1,
            match_name="FC København - Midtjylland",
            league="Dansk Superliga",
            sport="Fodbold",
            kickoff_time="I dag 19:00",
            odds={"1": 2.25, "X": 3.40, "2": 3.20},
            pinnacle_odds={"1": 2.05, "X": 3.45, "2": 3.50},
            bookmaker_afvigelse="Bet365 (1 = 2.25)",
            xg_home=1.85,
            xg_away=1.20,
            source="Pinnacle API + Live Feed"
        )
        m1.calculate_metrics(bankroll=1000.0)

        m2 = Match(
            match_id=2,
            match_name="Real Madrid - Barcelona",
            league="Spansk La Liga",
            sport="Fodbold",
            kickoff_time="I morgen 21:00",
            odds={"1": 2.05, "X": 3.60, "2": 3.40},
            pinnacle_odds={"1": 2.02, "X": 3.65, "2": 3.45},
            bookmaker_afvigelse="NordicBet (2 = 3.40)",
            xg_home=2.10,
            xg_away=1.95,
            source="Pinnacle API"
        )
        m2.calculate_metrics(bankroll=1000.0)

        m3 = Match(
            match_id=3,
            match_name="AGF - Brøndby IF",
            league="Dansk Superliga",
            sport="Fodbold",
            kickoff_time="Søndag 16:00",
            odds={"1": 2.90, "X": 3.20, "2": 2.50},
            pinnacle_odds={"1": 2.50, "X": 3.30, "2": 2.80},
            bookmaker_afvigelse="Unibet (1 = 2.90)",
            xg_home=1.40,
            xg_away=1.10,
            source="Testdata"
        )
        m3.calculate_metrics(bankroll=1000.0)

        m4 = Match(
            match_id=4,
            match_name="Boston Celtics - LA Lakers",
            league="NBA",
            sport="Basketball",
            kickoff_time="I nat 01:30",
            odds={"1": 1.55, "X": 15.0, "2": 2.65},
            pinnacle_odds={"1": 1.42, "X": 16.0, "2": 2.95},
            bookmaker_afvigelse="Danske Spil (1 = 1.55)",
            xg_home=112.5,
            xg_away=104.0,
            source="NBA Odds Feed"
        )
        m4.calculate_metrics(bankroll=1000.0)

        self.matches = [m1, m2, m3, m4]

        self.clv_history = [
            {
                "date": "01/11/2026",
                "match": "Arsenal - Chelsea",
                "pick": "Arsenal 1 @ 1.85",
                "placedOdds": 1.85,
                "closingOdds": 1.70,
                "clvPercent": "+8.8%",
                "status": "Vundet",
                "clvBeaten": True
            },
            {
                "date": "03/11/2026",
                "match": "Dortmund - Bayern München",
                "pick": "Over 3.5 Mål @ 2.10",
                "placedOdds": 2.10,
                "closingOdds": 1.92,
                "clvPercent": "+9.3%",
                "status": "Vundet",
                "clvBeaten": True
            },
            {
                "date": "04/11/2026",
                "match": "Bakken Bears - Team FOG Næstved",
                "pick": "Bakken Bears -6.5 @ 1.90",
                "placedOdds": 1.90,
                "closingOdds": 1.88,
                "clvPercent": "+1.1%",
                "status": "Tabt",
                "clvBeaten": True
            }
        ]

    def get_analyzed_matches(self, sport_filter: str = None, min_dev: float = 0.0) -> List[dict]:
        results = []
        for m in self.matches:
            dict_m = m.to_dict()
            if sport_filter and sport_filter != "Alle" and dict_m["sport"] != sport_filter:
                continue
            if dict_m["deviationPercent"] < min_dev:
                continue
            results.append(dict_m)
        return results

    def add_manual_match(
        self, name: str, league: str, sport: str, 
        o1: float, oX: float, o2: float, 
        p1: float, pX: float, p2: float, 
        bookmaker: str, bankroll: float = 1000.0,
        xg_home: float = 1.5, xg_away: float = 1.2
    ) -> dict:
        new_id = len(self.matches) + 1
        m = Match(
            match_id=new_id,
            match_name=name,
            league=league,
            sport=sport,
            kickoff_time="Manuelt indtastet",
            odds={"1": o1, "X": oX, "2": o2},
            pinnacle_odds={"1": p1, "X": pX, "2": p2},
            bookmaker_afvigelse=f"{bookmaker} (Manuel)",
            xg_home=xg_home,
            xg_away=xg_away,
            source="Brugerinput"
        )
        m.calculate_metrics(bankroll=bankroll)
        self.matches.insert(0, m)
        return m.to_dict()

    def get_daily_picks(self) -> List[dict]:
        analyzed = [m.to_dict() for m in self.matches if m.status in ["warning", "alert"]]
        
        c1_matches = []
        if len(analyzed) > 0:
            m1 = analyzed[0]
            c1_matches.append({
                "game": m1["match"],
                "pick": f"Option {m1['bestSelection']}",
                "odds": m1["bestOdds"],
                "reason": f"Pinnacle Benchmark afvigelse: +{m1['deviationPercent']}%. xG Rating ({m1['xgHome']} - {m1['xgAway']}). Anbefalet Kelly: {m1['recommendedBet']} kr."
            })
        c1_matches.append({
            "game": "Real Madrid - Barcelona",
            "pick": "Over 1.5 mål",
            "odds": 1.22,
            "reason": "xG Screening: Kombineret forventet xG på 4.05. 88% historisk overgang."
        })
        c1_matches.append({
            "game": "Arsenal - Chelsea",
            "pick": "1X (Dobbeltchance)",
            "odds": 1.60,
            "reason": "Ubesejret i 12 hjemmekampe. xG defensiv stabilitet (0.85 xGA hjemme)."
        })

        c2_matches = []
        if len(analyzed) > 1:
            m2 = analyzed[1]
            c2_matches.append({
                "game": m2["match"],
                "pick": f"Option {m2['bestSelection']}",
                "odds": m2["bestOdds"],
                "reason": f"System Value Spike: +{m2['deviationPercent']}% vs Pinnacle True Odds. Kelly stake: {m2['kellyStakePct']}%."
            })
        else:
            c2_matches.append({
                "game": "Malmö FF - AIK",
                "pick": "1 (Hjemmesejr)",
                "odds": 1.95,
                "reason": "Stærk hjemmebaneform. xG net-margin +1.15 pr kamp."
            })
        c2_matches.append({
            "game": "AGF - Brøndby IF",
            "pick": "Under 2.5 mål",
            "odds": 1.78,
            "reason": "xG defensiv tæthed i de seneste 5 indbyrdes opgør (samlet xG < 2.0)."
        })
        c2_matches.append({
            "game": "NBA: Boston Celtics - LA Lakers",
            "pick": "Celtics -4.5",
            "odds": 1.40,
            "reason": "Net Rating +8.4 på hjemmebane vs. Lakers b2b udebane."
        })

        c1 = DailyCoupon("Kupon 1 – Dynamisk xG & Pinnacle Værdi", c1_matches)
        c2 = DailyCoupon("Kupon 2 – Værdi & Afvigelse (High Value)", c2_matches)
        return [c1.to_dict(), c2.to_dict()]

    def get_clv_history(self) -> List[dict]:
        return self.clv_history
