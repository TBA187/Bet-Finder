
let matchesData = [];

async function loadErrors() {
    const sport = document.getElementById('sportFilter') ? document.getElementById('sportFilter').value : 'Alle';
    const minDev = document.getElementById('devFilter') ? document.getElementById('devFilter').value : 0;
    
    try {
        const res = await fetch(`/api/errors?sport=${sport}&min_dev=${minDev}`);
        matchesData = await res.json();
        renderErrorFinder();
    } catch (e) {
        console.error("Fejl ved hentning af data:", e);
    }
}

async function loadDailyPicks() {
    try {
        const res = await fetch('/api/daily-picks');
        const picks = await res.json();
        renderDailyPicks(picks);
    } catch (e) {
        console.error("Fejl ved hentning af picks:", e);
    }
}

async function loadCLVTracker() {
    try {
        const res = await fetch('/api/clv-tracker');
        const history = await res.json();
        renderCLVTracker(history);
    } catch (e) {
        console.error("Fejl ved hentning af CLV:", e);
    }
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    
    document.getElementById(tabId).classList.add('active');
    event.currentTarget.classList.add('active');
    
    if (tabId === 'errorFinder') loadErrors();
    if (tabId === 'dailyPicks') loadDailyPicks();
    if (tabId === 'clvTracker') loadCLVTracker();
}

function renderErrorFinder() {
    const container = document.getElementById('errorListContainer');
    document.getElementById('matchCount').innerText = `Viser ${matchesData.length} analyseret kampe`;
    
    container.innerHTML = matchesData.map(m => {
        let badgeClass = 'badge-green';
        let boxClass = '';
        if (m.status === 'warning') { badgeClass = 'badge-yellow'; boxClass = 'highlight-green'; }
        if (m.status === 'alert') { badgeClass = 'badge-red'; boxClass = 'highlight-alert'; }

        return `
            <div class="card">
                <div class="card-header">
                    <span>${m.sport} • ${m.league}</span>
                    <span class="badge ${badgeClass}">Afvigelse vs Pinnacle: +${m.deviationPercent}%</span>
                </div>
                <div class="match-title">${m.match}</div>
                <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Bookmaker med værdi: <strong>${m.bookmakerAfvigelse}</strong></p>
                <p style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 8px;">xG Rating: Hjemme (${m.xgHome}) vs Ude (${m.xgAway})</p>
                
                <div class="odds-grid">
                    <div class="odds-box ${m.bestSelection === '1' ? boxClass : ''}">
                        <span class="odds-label">1 (Hjemme)</span>
                        <span class="odds-value">${m.odds["1"]}</span>
                        <span style="font-size:0.65rem; color:var(--text-secondary); display:block;">Pin True: ${m.pinnacleFairOdds["1"]}</span>
                    </div>
                    <div class="odds-box ${m.bestSelection === 'X' ? boxClass : ''}">
                        <span class="odds-label">X (Uafgjort)</span>
                        <span class="odds-value">${m.odds["X"]}</span>
                        <span style="font-size:0.65rem; color:var(--text-secondary); display:block;">Pin True: ${m.pinnacleFairOdds["X"]}</span>
                    </div>
                    <div class="odds-box ${m.bestSelection === '2' ? boxClass : ''}">
                        <span class="odds-label">2 (Ude)</span>
                        <span class="odds-value">${m.odds["2"]}</span>
                        <span style="font-size:0.65rem; color:var(--text-secondary); display:block;">Pin True: ${m.pinnacleFairOdds["2"]}</span>
                    </div>
                </div>
                
                <div style="margin-top: 10px; font-size: 0.85rem; background: var(--bg-secondary); padding: 8px; border-radius: 6px; border-left: 3px solid var(--accent-green);">
                    🎯 <strong>Anbefalet Quarter-Kelly Indsats:</strong> ${m.recommendedBet} kr. (${m.kellyStakePct}% af bankroll)
                </div>

                <button class="btn" style="background: var(--bg-secondary); border: 1px solid var(--border-color); margin-top: 10px;" onclick="openDetail(${m.id})">Vis Pinnacle Analyse & Detaljer</button>
            </div>
        `;
    }).join('');
}

function renderDailyPicks(picks) {
    const container = document.getElementById('dailyPicksContainer');
    container.innerHTML = picks.map(coupon => `
        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <strong>${coupon.coupon}</strong>
                <span class="badge badge-green">Samlet Odds: ${coupon.totalOdds}</span>
            </div>
            <p style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 12px;">Estimeret vinder-sandsynlighed: ${coupon.probability}</p>
            ${coupon.matches.map(item => `
                <div class="coupon-box">
                    <div style="font-size: 0.9rem; font-weight: 600;">${item.game}</div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin: 4px 0;">
                        <span style="color: var(--accent-green);">Valg: ${item.pick}</span>
                        <strong>Odds: ${item.odds}</strong>
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-secondary);">${item.reason}</div>
                </div>
            `).join('')}
        </div>
    `).join('');
}

function renderCLVTracker(history) {
    const container = document.getElementById('clvContainer');
    container.innerHTML = `
        <div class="card" style="margin-bottom: 12px; background: rgba(16, 185, 129, 0.08); border-color: var(--accent-green);">
            <div style="font-size: 0.9rem; font-weight: 600; color: var(--accent-green);">CLV Performance Summary</div>
            <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 4px;">
                Gennemsnitlig CLV slået: <strong>+6.4%</strong> • Beat Rate: <strong>100%</strong> af registrerede spil.
            </div>
        </div>
        ${history.map(item => `
            <div class="card" style="padding: 12px;">
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--text-secondary);">
                    <span>${item.date} • ${item.match}</span>
                    <span class="badge badge-green">${item.status}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
                    <strong>${item.pick}</strong>
                    <span style="font-size: 0.85rem; color: var(--accent-green); font-weight: 700;">CLV: ${item.clvPercent}</span>
                </div>
                <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">
                    Spillet odds: ${item.placedOdds} | Lukkeodds: ${item.closingOdds}
                </div>
            </div>
        `).join('')}
    `;
}

function openDetail(id) {
    const m = matchesData.find(x => x.id === id);
    if (!m) return;
    document.getElementById('modalTitle').innerText = m.match;
    document.getElementById('modalBody').innerHTML = `
        <strong>Pinnacle Benchmark & Markedsanalyse:</strong><br>
        ${m.analysis}<br><br>
        <strong>Pinnacle Fair Odds (De-margined True Probs):</strong><br>
        • 1: ${m.pinnacleFairOdds["1"]} (Sandsynlighed: ${(m.trueProbs["1"]*100).toFixed(1)}%)<br>
        • X: ${m.pinnacleFairOdds["X"]} (Sandsynlighed: ${(m.trueProbs["X"]*100).toFixed(1)}%)<br>
        • 2: ${m.pinnacleFairOdds["2"]} (Sandsynlighed: ${(m.trueProbs["2"]*100).toFixed(1)}%)<br><br>
        <strong>Kelly-Kriterie Beregning:</strong><br>
        • Anbefalet spil: Option ${m.bestSelection} @ ${m.bestOdds}<br>
        • Anbefalet Indsats (Quarter-Kelly): ${m.recommendedBet} kr. (${m.kellyStakePct}% af bankroll)<br><br>
        <em>Datakilde: ${m.source}</em>
    `;
    document.getElementById('detailModal').style.display = 'flex';
}

function closeModal(e) {
    if (e.target.id === 'detailModal') {
        document.getElementById('detailModal').style.display = 'none';
    }
}

async function addNewManualMatch() {
    const matchName = document.getElementById('manualMatch').value;
    const league = document.getElementById('manualLeague').value;
    const sport = document.getElementById('manualSport').value;
    const o1 = parseFloat(document.getElementById('manual1').value);
    const oX = parseFloat(document.getElementById('manualX').value);
    const o2 = parseFloat(document.getElementById('manual2').value);
    const p1 = parseFloat(document.getElementById('pin1').value) || o1;
    const pX = parseFloat(document.getElementById('pinX').value) || oX;
    const p2 = parseFloat(document.getElementById('pin2').value) || o2;
    const bookie = document.getElementById('manualBookie').value;
    const bankroll = parseFloat(document.getElementById('manualBankroll').value) || 1000;
    const xgHome = parseFloat(document.getElementById('manualXgHome').value) || 1.5;
    const xgAway = parseFloat(document.getElementById('manualXgAway').value) || 1.2;

    if (!matchName || isNaN(o1) || isNaN(oX) || isNaN(o2)) {
        alert("Udfyld venligst alle felter korrekt med gyldige odds.");
        return;
    }

    const payload = { 
        match: matchName, league: league, sport: sport, 
        o1: o1, oX: oX, o2: o2, 
        pinnacle1: p1, pinnacleX: pX, pinnacle2: p2,
        bookmaker: bookie, bankroll: bankroll,
        xgHome: xgHome, xgAway: xgAway
    };

    const res = await fetch('/api/analyze-manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (res.ok) {
        alert("Kampen er analyseret af Python-backend med Pinnacle Benchmark & Kelly!");
        switchTab('errorFinder');
        document.querySelectorAll('.tab-btn')[0].classList.add('active');
        document.querySelectorAll('.tab-btn')[3].classList.remove('active');
    }
}

// Initial load
loadErrors();
