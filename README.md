# BetFinder 1.0 Ultimate Pro Edition

BetFinder 1.0 er en professionel, full-stack webapplikation og odds-analyse-engine udviklet i Python (FastAPI) med en PWA-klar (Progressive Web App) frontend. Systemet er opbygget til at identificere potentielle fejlprissætninger hos europæiske bookmakere, beregne sande fair odds og estimere den optimale indsats ved hjælp af avancerede statistiske modeller.

---

## 🚀 Implementerede Funktioner og Moduler

### 1. Bookmaker Error Finder (Prioritet 1)
- **Pinnacle Benchmark (True Odds De-margining):**
  Systemet renser automatisk overmarginen (husets fordel) fra Pinnacles skarpe odds for at etablere markedets sande vinder-sandsynligheder (*True Probabilities*). Afvigelser beregnes i forhold til Pinnacles rene fair odds.
- **Fejlkategorisering og Farvekoder:**
  - **Grøn / Normal:** Almindelige markedsvariationer (< 5%).
  - **Gul / Warning (Advarsel):** Afvigelser på 5-10% (mulig forsinket opdatering eller overreaktion).
  - **Rød / Alert (Alarm):** Afvigelser over 10% (potentiel tastefejl eller markant fejlprissætning).
- **Odds & Margin Beregninger:**
  Beregner automatisk husets samlede overmargin (%) for hver enkelt kamp og marked.

### 2. Optimal Indsatsberegning (Fractional Kelly-Kriterie)
- Implementering af **Quarter-Kelly formlen**:
  $$f^* = \frac{b \cdot p - q}{b} \times 0.25$$
- Anbefaler den eksakte indsatsstørrelse i DKK baseret på din angivne bankroll og den identificerede værdi-fordel.

### 3. xG-Baseret Screening (Expected Goals)
- Integreret rating af holdenes underliggende chance-skabelse (xG for og imod).
- Kombinerer xG-data med odds-afvigelser for at verificere, om et oddsafvig er et reelt værdispil.

### 4. Dynamiske Daily Picks (Prioritet 2)
- Genererer op til 2 daglige kuponforslag (hver bestående af 2-3 kampe med samlet odds over 3,00).
- **Dynamisk scanning:** Kuponerne sammensættes automatisk ud fra de bedst vurderede Kelly-værdier og xG-scorer.
- **Korrelationsbeskyttelse:** Forhindrer statistisk afhængige valg på samme kupon.

### 5. Closing Line Value (CLV) Tracker
- Dedikeret modul til overvågning af spilhistorik.
- Måler om placerede spil slår lukke-oddset (CLV), hvilket er den ultimative indikator for langsigtede vinder-odds.

### 6. Avanceret Filtrering & Multi-Sport Support
- Interaktiv sortering på dashboardet efter **Sport** (Fodbold, Basketball osv.) og **Minimum Afvigelses-%** (f.eks. kun vise 10%+ afvigelser).
- Støtte for ligaer som Dansk Superliga, La Liga, Champions League, Allsvenskan, NBA og Dansk Basketliga.

---

## 🛠️ Roadmap: Fremtidige Forslag (Til Implementering i v2.0+)

Følgende funktioner og udvidelser er planlagt til næste udviklingsfase, når automatiske kommercielle API-nøgler tilsluttes:

1. **Automatiske Push-Notifikationer (Web Push / Service Worker):**
   - Direkte besked på iPhone/Android, når systemet registrerer en rød fejlprissætning (> 10% afvigelse) i realtid.
2. **Kloakering & Målalarm (Arbitrage Spike Detection):**
   - Automatisk detektion af tidsforsinkelser hos langsomme bookmakere umiddelbart efter scoringer eller røde kort.
3. **Direkte Bookmaker API-Integrationer:**
   - Integration med *The Odds API* eller *Pinnacle Direct API* for fuldautomatisk indhentning af odds fra NordicBet, Danske Spil, Bet365, Unibet m.fl. uden manuel indtastning.
4. **SQLite / PostgreSQL Database Integration:**
   - Erstatning af in-memory datastrukturen med en permanent database for historisk gemning af tusindvis af analyser og brugerkonti.
5. **Kuponbygger & Bet Builder Simulator:**
   - Værktøj til beregning af kompleks korrelation ved kombinationsspil på samme kamp.

---

## 📦 Installations- og Kørselsvejledning

### Forudsætninger
- Python 3.9+ installeret.

### Lokal Afvikling (Development)
1. Pak ZIP-filen ud.
2. Åbn din terminal i mappen og installer afhængighederne:
   ```bash
   pip install -r requirements.txt
   ```
3. Start FastAPI-serveren:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
4. Åbn browseren på `http://localhost:8000`.

### Upload til Remote Server (Production)

#### Mulighed 1: Render / Vercel / Railway / PythonAnywhere (Gratis Hosting)
1. Opret et nyt projekt og tilslut kildemappen.
2. Sæt **Build Command** til: `pip install -r requirements.txt`.
3. Sæt **Start Command** til: `uvicorn main:app --host 0.0.0.0 --port 8000`.

#### Mulighed 2: Linux-server med Nginx & Systemd (cPanel / SSH)
1. Upload filerne via FTP/SSH til din remote server.
2. Opret et virtual environment og installer kravene:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Kør applikationen via Uvicorn eller Gunicorn med Uvicorn workers.
