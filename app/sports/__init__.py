from __future__ import annotations

from typing import Optional

from app.sports.base import SportModel
from app.sports.football import FootballModel
from app.sports.normal_model import BasketballModel, HandballModel

_MODELS = {"football": FootballModel, "basketball": BasketballModel, "handball": HandballModel}


def model_for(sport: str, params: Optional[dict] = None) -> SportModel:
    try:
        return _MODELS[sport](params)
    except KeyError:
        raise ValueError(f"Unsupported sport {sport!r}") from None
