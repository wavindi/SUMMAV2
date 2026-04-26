// ============================================================================
// 1. NETWORK & API CONFIGURATION
// ============================================================================

const API_BASE = window.location.protocol.startsWith('http') ? window.location.origin : "http://127.0.0.1:5000";
console.log(`📡 API Configured at: ${API_BASE}`);

function uiEventId() {
    const rnd = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    return `ui-${rnd}`;
}

function postRemoteEvent(payload) {
    return fetch(`${API_BASE}/remote_event`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(async (res) => {
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            console.error(`❌ /remote_event ${payload.action} failed: HTTP ${res.status} ${text}`);
        }
        return res;
    }).catch(error => {
        console.error(`❌ /remote_event ${payload.action} network error:`, error);
    });
}

// ============================================================================
// 2. GLOBAL CONTROL FUNCTIONS
// ============================================================================

window.addPointManual = function(team) {
    console.log(`🔘 CLICK: Add Point for ${team}`);
    animateButton();
    postRemoteEvent({action: 'addpoint', team: team, event_id: uiEventId()});
};

window.subtractPoint = function(team) {
    console.log(`🔘 CLICK: Subtract Point for ${team}`);
    animateButton();
    postRemoteEvent({action: 'subtractpoint', team: team, event_id: uiEventId()});
};

window.resetMatch = function() {
    console.log(`🔘 CLICK: Reset Request`);
    animateButton();
    if(confirm("Are you sure you want to reset the match?")) {
        resetMatchAndGoToSplash();
    }
};

function animateButton() {
    const btn = document.activeElement;
    if(btn && btn.classList.contains('control-btn')) {
        btn.style.transform = "scale(0.95)";
        setTimeout(() => btn.style.transform = "scale(1)", 100);
    }
}

// ============================================================================
// 3. GAME VARIABLES
// ============================================================================

let score1 = 0;
let score2 = 0;
let games1 = 0;
let games2 = 0;
let sets1 = 0;
let sets2 = 0;
let matchWon = false;
let winnerData = null;
let setsHistory = [];

// TIME SYNC VARIABLES
let serverMatchStartTime = null; 

let splashDismissed = false;

// TIMEOUT VARIABLES
let winnerDismissTimeout = null;
let stageTimeout = null;
let sideSwitchTimeout = null;
let timerInterval = null;

let gameMode = null;
let isScoreboardActive = false;
let matchWonFlag = false;

// ============================================================================
// 4. SOCKET.IO CONNECTION
// ============================================================================

let socket;
if (typeof io !== 'undefined') {
    socket = io(API_BASE, {
        transports: ["polling", "websocket"],
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: 10
    });

    socket.on('connect', () => {
        console.log('✅ Socket.IO CONNECTED');
        socket.emit('request_gamestate');
    });
    
    socket.on('disconnect', (reason) => console.error('❌ Socket.IO DISCONNECTED:', reason));
    socket.on('connect_error', (error) => console.error('❌ Socket.IO CONNECTION ERROR:', error));
    
    socket.on('gamestateupdate', (data) => updateFromGameState(data));
    socket.on('pointscored', (data) => handleSensorInput(data));
    socket.on('matchwon', (data) => {
        if (typeof displayWinner === 'function') displayWinner(data);
    });
    socket.on('sideswitchrequired', (data) => handleSideSwitch(data));
    socket.on('sensor_validation_result', (data) => console.log('Sensor validation:', data));

    // V2: server-side pygame removed; play change.mp3 in the browser on side-switch
    socket.on('play_change_audio', () => {
        try {
            const a = new Audio('change.mp3');
            a.volume = 1.0;
            a.play().catch(e => console.warn('audio play blocked:', e));
        } catch (e) { console.warn('audio error', e); }
    });

    // V2: per-team online badge driven by ESP32 heartbeat stream
    socket.on('sensor_heartbeat', (snapshot) => {
        if (typeof renderHeartbeatBadges === 'function') renderHeartbeatBadges(snapshot);
    });

    socket.on('match_reset_triggered', () => {
        console.log("🔄 Reset triggered from sensor. Reloading UI...");
        window.location.reload();
    });

} else {
    console.error("❌ Socket.IO library not found! Real-time features disabled.");
}

// ============================================================================
// 5. INPUT HANDLERS
// ============================================================================

function toggleControlPanel() {
    const panel = document.getElementById('controlPanel');
    if (panel) {
        const isHidden = panel.style.display === 'none';
        panel.style.display = isHidden ? 'flex' : 'none';
        console.log(`Control Panel ${isHidden ? 'SHOWN' : 'HIDDEN'}`);
    }
}

document.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() === 'c') {
        toggleControlPanel();
    }
});

window.addEventListener('DOMContentLoaded', () => {
    const logo = document.getElementById('logoClick');
    if (logo) {
        logo.style.cursor = 'pointer';
        logo.addEventListener('click', (e) => {
            console.log("🖱️ Logo Clicked");
            e.preventDefault();
            e.stopPropagation();
            toggleControlPanel();
        });
    }
    
    setupTeamClickHandlers();
    
    const splashScreen = document.getElementById('splashScreen');
    if (splashScreen) {
        splashScreen.classList.add('active');
        splashScreen.addEventListener('click', () => {
            if (splashScreen.classList.contains('active')) dismissSplash();
        });
    }
    
    // START THE MATCH TIMER
    startMatchTimer();
    
    if(socket) socket.emit('request_gamestate');
});

function handleSensorInput(data) {
    const winnerDisplay = document.getElementById('winnerDisplay');
    if (winnerDisplay && winnerDisplay.style.display === 'flex') {
        // If on winner screen, sensor input resets the match
        clearWinnerTimeout(); 
        resetMatchAndGoToSplash();
        return;
    }

    const splashScreen = document.getElementById('splashScreen');
    if (splashScreen && splashScreen.classList.contains('active')) {
        dismissSplash();
        return;
    }

    const modeScreen = document.getElementById('modeSelectionScreen');
    if (modeScreen && modeScreen.style.display === 'flex') {
        if (data.action === 'addpoint') selectMode('basic');
        else if (data.action === 'subtractpoint') selectMode('competition');
        return;
    }

    if (isScoreboardActive && gameMode) {
        showClickFeedback(data.team); 
        showToast(data.action, data.team, data.gamestate); 
    }
}

// ============================================================================
// 6. UI LOGIC (Winner, Side Switch, Toast)
// ============================================================================

function handleSideSwitch(data) {
    if (matchWonFlag || matchWon) return;
    showSideSwitchNotification(data);
}

function showSideSwitchNotification(data) {
    const existing = document.getElementById('sideSwitchNotification');
    if (existing) existing.remove();
    if (sideSwitchTimeout) clearTimeout(sideSwitchTimeout);

    const notification = document.createElement('div');
    notification.id = 'sideSwitchNotification';
    notification.style.cssText = `
        position: fixed !important; top: 0; left: 0; width: 100vw; height: 100vh;
        background: rgba(0, 0, 0, 0.95); display: flex; flex-direction: column;
        justify-content: center; align-items: center; z-index: 99999; cursor: pointer;
    `;

    notification.innerHTML = `
        <div style="font-family: 'Anton', Arial, sans-serif; font-style: italic; text-align: center;">
            <div style="font-size: 120px; color: #d4af37; margin-bottom: 30px;">🔄</div>
            <div style="font-size: 80px; color: #d4af37; text-transform: uppercase; letter-spacing: 10px; margin-bottom: 20px;">CHANGE SIDES</div>
            <div style="font-size: 60px; color: white; margin-bottom: 40px;">Total Games: ${data.totalgames || 0}</div>
            <div style="font-size: 48px; color: rgba(255, 255, 255, 0.7);">Score: ${data.gamescore || '0-0'}</div>
            <div style="font-size: 24px; color: rgba(212, 175, 55, 0.8); margin-top: 50px; text-transform: uppercase;">Dismissing in 6 seconds...</div>
        </div>
    `;

    document.body.appendChild(notification);

    const dismissNotification = () => {
        const notif = document.getElementById('sideSwitchNotification');
        if (notif) notif.remove();
        if (sideSwitchTimeout) clearTimeout(sideSwitchTimeout);
        if(socket) socket.off('pointscored', sensorDismissHandler);
    };

    sideSwitchTimeout = setTimeout(dismissNotification, 6000);

    let sensorReady = false;
    setTimeout(() => { sensorReady = true; }, 500);
    const sensorDismissHandler = () => { if (sensorReady) dismissNotification(); };
    
    if(socket) socket.on('pointscored', sensorDismissHandler);
    notification.addEventListener('click', dismissNotification);
}

function displayWinner(data) {
    if (!data) return;
    const winnerDisplay = document.getElementById('winnerDisplay');
    if (!winnerDisplay) return;

    matchWonFlag = true;
    matchWon = true;

    // Stop the timer
    if (timerInterval) clearInterval(timerInterval);

    document.querySelector('.scoreboard')?.classList.add('hidden');

    const winnerTeam = data.matchdata?.winnerteam || data.winner?.team || 'black';
    const winnerName = data.matchdata?.winnername || data.winner?.teamname || 'UNKNOWN';

    // Populate UI
    winnerDisplay.querySelector('.winner-team-name').textContent = winnerName;
    winnerDisplay.querySelector('.winner-team-name').className = `winner-team-name ${winnerTeam}`;
    winnerDisplay.querySelector('.winner-team-name-small').textContent = winnerName;
    
    const stats = winnerDisplay.querySelectorAll('.stat-value');
    if (stats.length >= 2) {
        stats[0].textContent = data.matchdata?.finalsetsscore || '0-0';
        stats[1].textContent = data.matchdata?.matchduration || 'N/A';
    }

    const tbody = winnerDisplay.querySelector('.sets-table tbody');
    tbody.innerHTML = '';
    (data.matchdata?.setsbreakdown || []).forEach(set => {
        const row = document.createElement('tr');
        const blackWin = set.setwinner === 'black';
        row.innerHTML = `
            <td>Set ${set.setnumber}</td>
            <td style="${blackWin ? 'color:var(--gold);font-weight:700' : ''}">${set.blackgames}</td>
            <td style="${!blackWin ? 'color:var(--gold);font-weight:700' : ''}">${set.yellowgames}</td>
            <td>${set.setwinner.toUpperCase()}</td>
        `;
        tbody.appendChild(row);
    });

    const stage1 = winnerDisplay.querySelector('.winner-stage-1');
    const stage2 = winnerDisplay.querySelector('.winner-stage-2');

    // INITIAL STATE: SHOW STAGE 1
    stage1.style.display = 'flex';
    stage2.style.display = 'none';
    stage1.classList.remove('fade-out');
    winnerDisplay.style.display = 'flex';
    
    clearWinnerTimeout();

    // --- CLICK LOGIC ---
    let currentStage = 1;

    const goToStage2 = () => {
        if(currentStage !== 1) return;
        currentStage = 2;

        stage1.classList.add('fade-out');
        setTimeout(() => {
            stage1.style.display = 'none';
            stage2.style.display = 'flex';
        }, 500);

        // Reset timer for another 30s
        if (winnerDismissTimeout) clearTimeout(winnerDismissTimeout);
        if (stageTimeout) clearTimeout(stageTimeout);
        
        console.log("⏳ Stage 2 Started: Auto-reset in 30s");
        winnerDismissTimeout = setTimeout(resetMatchAndGoToSplash, 30000);
    };

    winnerDisplay.onclick = (e) => {
        e.stopPropagation();
        if (currentStage === 1) {
            console.log("🖱️ Clicked Stage 1 -> Skipping to Stage 2");
            goToStage2();
        } else {
            console.log("🖱️ Clicked Stage 2 -> Resetting Match");
            clearWinnerTimeout();
            resetMatchAndGoToSplash();
        }
    };

    // --- AUTO TIMERS ---
    // 30 Seconds for Stage 1 -> Go to Stage 2
    stageTimeout = setTimeout(() => {
        console.log("⏰ Stage 1 Timeout -> Going to Stage 2");
        goToStage2();
    }, 30000); 

    // Safety fallback (65s total)
    winnerDismissTimeout = setTimeout(resetMatchAndGoToSplash, 65000);

    fetch(`${API_BASE}/markmatchdisplayed`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({wipe_immediately: false})
    }).catch(e => console.error(e));
}

// ============================================================================
// 7. HELPERS
// ============================================================================

function updateFromGameState(data) {
    score1 = data.score1; score2 = data.score2;
    games1 = data.game1; games2 = data.game2;
    sets1 = data.set1; sets2 = data.set2;
    matchWon = data.matchwon; matchWonFlag = data.matchwon;
    gameMode = data.gamemode;
    
    // SYNC TIME FROM SERVER
    if (data.matchstarttime) {
        serverMatchStartTime = data.matchstarttime;
    }
    
    document.querySelector('.black-team .score-main').textContent = score1;
    document.querySelector('.yellow-team .score-main').textContent = score2;
    document.querySelector('.black-team .team-games').textContent = games1;
    document.querySelector('.yellow-team .team-games').textContent = games2;
    const setEls = document.querySelectorAll('.sets-score-number');
    if(setEls.length >= 2) { setEls[0].textContent = sets1; setEls[1].textContent = sets2; }
}

function startMatchTimer() {
    const display = document.getElementById('timeDisplay');
    if (!display) return;

    if (timerInterval) clearInterval(timerInterval);

    timerInterval = setInterval(() => {
        if (!serverMatchStartTime || matchWonFlag) {
            // If match hasn't started or is done, don't update (or could show 00:00)
            if (!serverMatchStartTime) display.textContent = "00:00";
            return;
        }

        const start = new Date(serverMatchStartTime).getTime();
        const now = new Date().getTime();
        const diff = Math.floor((now - start) / 1000);

        if (diff < 0) {
            display.textContent = "00:00";
            return;
        }

        const minutes = Math.floor(diff / 60);
        const seconds = diff % 60;
        
        const minStr = String(minutes).padStart(2, '0');
        const secStr = String(seconds).padStart(2, '0');
        
        display.textContent = `${minStr}:${secStr}`;
    }, 1000);
}

function showClickFeedback(team) {
    // Feature disabled: +1 animation removed
    return;
}

function showToast(action, team, gamestate) {
    // FILTER: Stop here if it's just a normal point
    if (action !== 'game' && action !== 'set') return;

    const container = document.getElementById('toastContainer') || createToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast toast-${getToastType(action)}`;

    const icon = getToastIcon(action);
    const title = getToastTitle(action, team);
    // Message is now empty as requested
    const message = ""; 

    toast.innerHTML = `
        <div class="toast-icon">${icon}</div>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-message">${message}</div>
        </div>
        <div class="toast-close" onclick="this.parentElement.remove()">×</div>
    `;
    container.appendChild(toast);
    setTimeout(() => { toast.remove(); }, 3000);
}

function createToastContainer() {
    const c = document.createElement('div');
    c.id = 'toastContainer'; c.className = 'toast-container';
    document.body.appendChild(c);
    return c;
}

function getToastType(action) {
    if (action.includes('set')) return 'set';
    if (action.includes('game')) return 'game';
    return 'point';
}

function getToastIcon(action) {
    if (action.includes('set')) return '🎾';
    if (action.includes('game')) return '🏆';
    return '✓';
}

function getToastTitle(action, team) {
    const teamName = team.toUpperCase();
    if (action.includes('set')) return `${teamName} WINS SET`;
    if (action.includes('game')) return `${teamName} WINS GAME`;
    return `${teamName} SCORES`;
}

// Function kept for compatibility but returns empty string
function getToastMessage(action, gamestate) {
    return "";
}

function selectMode(mode) {
    gameMode = mode;
    fetch(`${API_BASE}/setgamemode`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode: mode})
    }).then(r => r.json()).then(() => {
        const screen = document.getElementById('modeSelectionScreen');
        screen.classList.remove('active');
        screen.style.display = 'none';
        document.querySelector('.scoreboard').classList.remove('hidden');
        isScoreboardActive = true;
    });
}

function dismissSplash() {
    const splash = document.getElementById('splashScreen');
    splash.classList.remove('active');
    splash.style.display = 'none';
    const mode = document.getElementById('modeSelectionScreen');
    mode.style.display = 'flex';
    mode.classList.add('active');
}

function resetMatchAndGoToSplash() {
    // 1. Force visual reset immediately
    const display = document.getElementById('timeDisplay');
    if (display) display.textContent = "00:00";
    
    // 2. Clear the running timer
    if (timerInterval) clearInterval(timerInterval);
    serverMatchStartTime = null;

    // 3. Tell backend to reset and reload
    postRemoteEvent({action: 'reset', event_id: uiEventId()})
    .then(() => window.location.reload());
}

function clearWinnerTimeout() {
    if (winnerDismissTimeout) clearTimeout(winnerDismissTimeout);
    if (stageTimeout) clearTimeout(stageTimeout);
}

function setupTeamClickHandlers() {
    document.querySelector('.black-team')?.addEventListener('click', (e) => {
        if (!e.target.closest('.control-panel')) handleTeamClick('black');
    });
    document.querySelector('.yellow-team')?.addEventListener('click', (e) => {
        if (!e.target.closest('.control-panel')) handleTeamClick('yellow');
    });
}

function handleTeamClick(team) {
    if (document.getElementById('modeSelectionScreen').style.display === 'flex') {
        selectMode(team === 'black' ? 'basic' : 'competition');
    } else if (isScoreboardActive && gameMode) {
        window.addPointManual(team);
    }
}

// V2: tiny per-team online badge fed by backend /sensor_heartbeat snapshots
function renderHeartbeatBadges(snapshot) {
    const ensure = (team) => {
        const sel = team === 'black' ? '.black-team' : '.yellow-team';
        const root = document.querySelector(sel);
        if (!root) return null;
        let badge = root.querySelector('.sensor-badge');
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'sensor-badge';
            badge.style.cssText = 'position:absolute;top:8px;' +
                (team === 'black' ? 'left:8px;' : 'right:8px;') +
                'font-size:11px;padding:2px 6px;border-radius:10px;' +
                'background:rgba(0,0,0,0.55);color:#0f0;z-index:20;';
            if (getComputedStyle(root).position === 'static') root.style.position = 'relative';
            root.appendChild(badge);
        }
        return badge;
    };
    const byTeam = { black: null, yellow: null };
    Object.values(snapshot || {}).forEach(h => { if (h.team) byTeam[h.team] = h; });
    ['black', 'yellow'].forEach(team => {
        const badge = ensure(team);
        if (!badge) return;
        const h = byTeam[team];
        if (!h) { badge.textContent = '⚫ no node'; badge.style.color = '#888'; return; }
        const online = h.online;
        badge.textContent = online ? `● ${h.rssi ?? '?'}dBm` : '○ offline';
        badge.style.color = online ? '#0f0' : '#f55';
    });
}
