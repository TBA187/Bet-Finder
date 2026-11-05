from typing import Dict, Optional

class Match:
    def __init__(
        self,
        match_id: int,
        match_name: str,
        league: str,
        sport: str,
        kickoff_time: str,
        odds: Dict[str, float],
        pinnacle_odds: Dict[str, float],
        bookmaker_afvigelse: str,
        xg_home: float = 1.5,
        xg_away: float = 1.0,
        source: str = "Manuel/Testdata"
    ):
        self.match_id = match_id
        self.match_name = match_name
        self.league = league
        self.sport = sport
        self.kickoff_time = kickoff_time
        self.odds = odds
        self.pinnacle_odds = pinnacle_odds
        self.bookmaker_afvigelse = bookmaker_afvigelse
        self.xg_home = xg_home
        self.xg_away = xg_away
        self.source = source
        
        self.fair_odds: Dict[str, float] = {}
        self.true_probs: Dict[str, float] = {}
        self.margin: float = 0.0
        self.pinnacle_fair_odds: Dict[str, float] = {}
        self.max_deviation_percent: float = 0.0
        self.best_selection: str = ""
        self.best_odds: float = 1.0
        self.status: str = "normal"
        self.kelly_stake_pct: float = 0.0
        self.recommended_bet: float = 0.0

    def calculate_metrics(self, bankroll: float = 1000.0, kelly_fraction: float = 0.25):
        pin_total_prob = sum(1.0 / o for o in self.pinnacle_odds.values() if o > 0)
        for selection, odds_val in self.pinnacle_odds.items():
            raw_prob = 1.0 / odds_val
            fair_prob = raw_prob / pin_total_prob
            self.true_probs[selection] = round(fair_prob, 4)
            self.pinnacle_fair_odds[selection] = round(1.0 / fair_prob, 2)

        m_total_prob = sum(1.0 / o for o in self.odds.values() if o > 0)
        self.margin = round((m_total_prob - 1.0) * 100, 2)
        for selection, odds_val in self.odds.items():
            self.fair_odds[selection] = round(1.0 / ((1.0 / odds_val) / m_total_prob), 2)

        max_dev = -999.0
        best_sel = "1"
        best_o = self.odds["1"]

        for selection, odds_val in self.odds.items():
            true_o = self.pinnacle_fair_odds[selection]
            dev = ((odds_val / true_o) - 1.0) * 100
            if dev > max_dev:
                max_dev = dev
                best_sel = selection
                best_o = odds_val

        self.max_deviation_percent = round(max_dev, 1)
        self.best_selection = best_sel
        self.best_odds = best_o

        if self.max_deviation_percent >= 10.0:
            self.status = "alert"
        elif self.max_deviation_percent >= 5.0:
            self.status = "warning"
        else:
            self.status = "normal"

        b = self.best_odds - 1.0
        p = self.true_probs[self.best_selection]
        q = 1.0 - p
        
        if b > 0 and (b * p - q) > 0:
            full_kelly = (b * p - q) / b
            fractional_kelly = max(0.0, full_kelly * kelly_fraction)
            self.kelly_stake_pct = round(fractional_kelly * 100, 2)
            self.recommended_bet = round(bankroll * fractional_kelly, 2)
        else:
            self.kelly_stake_pct = 0.0
            self.recommended_bet = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.match_id,
            "match": self.match_name,
            "league": self.league,
            "sport": self.sport,
            "time": self.kickoff_time,
            "source": self.source,
            "odds": self.odds,
            "pinnacleOdds": self.pinnacle_odds,
            "pinnacleFairOdds": self.pinnacle_fair_odds,
            "fairOdds": self.fair_odds,
            "trueProbs": self.true_probs,
            "margin": self.margin,
            "xgHome": self.xg_home,
            "xgAway": self.xg_away,
            "bookmakerAfvigelse": self.bookmaker_afvigelse,
            "deviationPercent": self.max_deviation_percent,
            "bestSelection": self.best_selection,
            "bestOdds": self.best_odds,
            "status": self.status,
            "kellyStakePct": self.kelly_stake_pct,
            "recommendedBet": self.recommended_bet,
            "analysis": f"Pinnacle True Fair Odds for option {self.best_selection} er {self.pinnacle_fair_odds.get(self.best_selection)}. "
                        f"Udbyder har {self.best_odds} (+{self.max_deviation_percent}% afvigelse vs. Pinnacle Benchmark). "
                        f"xG Rating: Hjemme ({self.xg_home}) vs Ude ({self.xg_away}). "
                        f"Anbefalet Quarter-Kelly indsats: {self.kelly_stake_pct}% af bankroll."
        }
