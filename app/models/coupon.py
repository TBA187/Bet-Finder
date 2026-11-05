from typing import List, Dict

class DailyCoupon:
    def __init__(self, title: str, matches: List[Dict]):
        self.title = title
        self.matches = matches
        self.total_odds = self._calculate_total_odds()
        self.probability = self._calculate_probability()

    def _calculate_total_odds(self) -> float:
        total = 1.0
        for m in self.matches:
            total *= m.get("odds", 1.0)
        return round(total, 2)

    def _calculate_probability(self) -> str:
        if self.total_odds > 0:
            prob = (1.0 / self.total_odds) * 100
            return f"{round(prob, 1)}%"
        return "0%"

    def to_dict(self) -> dict:
        return {
            "coupon": self.title,
            "totalOdds": self.total_odds,
            "probability": self.probability,
            "matches": self.matches
        }
