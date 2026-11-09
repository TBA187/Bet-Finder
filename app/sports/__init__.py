from __future__ import annotations

from typing import Optional

from app.core.sports import SPORTS
from app.sports.base import SportModel
from app.sports.binary import BinaryModel
from app.sports.football import FootballModel, IceHockeyModel
from app.sports.normal_model import AmericanFootballModel, BaseballModel, BasketballModel, HandballModel

_MODELS = {
    "football": FootballModel,
    "icehockey": IceHockeyModel,
    "basketball": BasketballModel,
    "handball": HandballModel,
    "americanfootball": AmericanFootballModel,
    "baseball": BaseballModel,
    "binary": BinaryModel,
}


def model_for(sport: str, params: Optional[dict] = None) -> SportModel:
    try:
        cls = _MODELS[SPORTS[sport]["model"]]
    except KeyError:
        raise ValueError(f"Unsupported sport {sport!r}") from None
    model = cls(params)
    model.sport = sport
    return model
