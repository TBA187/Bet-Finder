from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.services.odd_service import OddsService

app = FastAPI(
    title="BetFinder 1.0 Ultimate Pro",
    description="Odds Error Finder, Pinnacle Benchmark, Kelly Criterion, xG & CLV Engine",
    version="2.0.0"
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

odds_service = OddsService()

@app.get("/")
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/errors")
async def get_errors(sport: str = None, min_dev: float = 0.0):
    return odds_service.get_analyzed_matches(sport_filter=sport, min_dev=min_dev)

@app.get("/api/daily-picks")
async def get_daily_picks():
    return odds_service.get_daily_picks()

@app.get("/api/clv-tracker")
async def get_clv_tracker():
    return odds_service.get_clv_history()

@app.post("/api/analyze-manual")
async def analyze_manual(data: dict):
    match_name = data.get("match", "")
    league = data.get("league", "")
    sport = data.get("sport", "Fodbold")
    o1 = float(data.get("o1", 1.0))
    oX = float(data.get("oX", 1.0))
    o2 = float(data.get("o2", 1.0))
    pinnacle_1 = float(data.get("pinnacle1", o1))
    pinnacle_X = float(data.get("pinnacleX", oX))
    pinnacle_2 = float(data.get("pinnacle2", o2))
    bookie = data.get("bookmaker", "Brugerinput")
    bankroll = float(data.get("bankroll", 1000.0))
    xg_home = float(data.get("xgHome", 1.5))
    xg_away = float(data.get("xgAway", 1.2))
    
    analyzed = odds_service.add_manual_match(
        match_name, league, sport, o1, oX, o2, 
        pinnacle_1, pinnacle_X, pinnacle_2, 
        bookie, bankroll, xg_home, xg_away
    )
    return analyzed
