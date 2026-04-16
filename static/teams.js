(function() {
'use strict';

// --- CONSTANTS ---
const ANIMATION_STAGGER_MS = 300;
const SHUFFLE_ITERATIONS = 6;
const SHUFFLE_INTERVAL_MS = 500;
const BATCH_ANIMATION_THRESHOLD = 20;

// --- DOM ELEMENTS ---
const phaseRegistration = document.getElementById('phase-registration');
const phaseAnimation    = document.getElementById('phase-animation');
const phaseTeams        = document.getElementById('phase-teams');
const playerListEl      = document.getElementById('player-list');
const playerCountEl     = document.getElementById('player-count');
const qrUrlEl           = document.getElementById('qr-url');
const teamColumnsEl     = document.getElementById('team-columns');
const scheduleBodyEl    = document.getElementById('schedule-body');

// Animation DOM
const animPlayerList    = document.getElementById('anim-player-list');
const animTeamColumns   = document.getElementById('anim-team-columns');

// --- STATE ---
let currentState = null;
let isAnimating = false;

// --- SSE ---
const eventSource = new EventSource('/api/team_stream');
eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleStateUpdate(data);
};

// --- QR URL ---
qrUrlEl.textContent = window.location.origin + '/register';

// --- STATE HANDLER ---
function handleStateUpdate(data) {
    const prevPhase = currentState ? currentState.phase : null;
    currentState = data;

    // Phase transition: registration -> teams_created triggers animation
    if (prevPhase === 'registration' && data.phase === 'teams_created' && !isAnimating) {
        runTeamAnimation(data);
        return;
    }

    renderPhase(data);
}


// --- PHASE RENDERING ---
function renderPhase(data) {
    if (data.phase === 'registration') {
        showPhase(phaseRegistration);
        renderPlayerList(data.players);
    } else {
        showPhase(phaseTeams);
        renderTeamColumns(data);
        renderSchedule(data.schedule);
    }
}

function showPhase(activePhase) {
    [phaseRegistration, phaseAnimation, phaseTeams].forEach(p => {
        p.classList.toggle('hidden', p !== activePhase);
    });
}


// --- PLAYER LIST ---
function renderPlayerList(players) {
    playerCountEl.textContent = players.length;

    // Build a set of current IDs to avoid full re-render
    const existingIds = new Set();
    playerListEl.querySelectorAll('.player-chip').forEach(chip => {
        existingIds.add(chip.dataset.playerId);
    });

    const newIds = new Set(players.map(p => p.id));

    // Remove chips that are no longer in the list
    playerListEl.querySelectorAll('.player-chip').forEach(chip => {
        if (!newIds.has(chip.dataset.playerId)) {
            chip.remove();
        }
    });

    // Add new chips
    players.forEach(player => {
        if (!existingIds.has(player.id)) {
            const chip = document.createElement('div');
            chip.className = 'player-chip';
            chip.dataset.playerId = player.id;

            if (player.position === 'goalkeeper') {
                const badge = document.createElement('span');
                badge.className = 'gk-badge';
                badge.textContent = 'TW';
                chip.appendChild(badge);
            }

            const nameSpan = document.createElement('span');
            nameSpan.textContent = player.name;

            chip.appendChild(nameSpan);
            playerListEl.appendChild(chip);
        }
    });
}

// --- TEAM COLUMNS ---
function renderTeamColumns(data) {
    const playerMap = {};
    const positionMap = {};
    data.players.forEach(p => {
        playerMap[p.id] = p.name;
        positionMap[p.id] = p.position;
    });

    teamColumnsEl.innerHTML = '';
    data.teams.forEach(team => {
        const col = document.createElement('div');
        col.className = 'team-col';

        const header = document.createElement('div');
        header.className = 'team-header';
        header.style.background = team.color;
        header.textContent = team.name;

        const body = document.createElement('div');
        body.className = 'team-body';

        team.players.forEach(pid => {
            const el = document.createElement('div');
            el.className = 'team-player';
            if (positionMap[pid] === 'goalkeeper') {
                const badge = document.createElement('span');
                badge.className = 'gk-badge';
                badge.textContent = 'TW';
                el.appendChild(badge);
            }
            const nameSpan = document.createTextNode(playerMap[pid] || pid);
            el.appendChild(nameSpan);
            body.appendChild(el);
        });

        col.appendChild(header);
        col.appendChild(body);
        teamColumnsEl.appendChild(col);
    });
}


// --- SCHEDULE ---
function renderSchedule(schedule) {
    scheduleBodyEl.innerHTML = '';
    schedule.forEach(match => {
        const tr = document.createElement('tr');

        const tdNum = document.createElement('td');
        tdNum.textContent = match.game;

        const tdHome = document.createElement('td');
        tdHome.textContent = match.home;

        const tdVs = document.createElement('td');
        tdVs.className = 'match-vs';
        tdVs.textContent = 'vs';

        const tdAway = document.createElement('td');
        tdAway.textContent = match.away;

        const tdScore = document.createElement('td');
        tdScore.className = 'match-score';
        if (match.score_home != null && match.score_away != null) {
            tdScore.textContent = match.score_home + ' : ' + match.score_away;
        }

        tr.appendChild(tdNum);
        tr.appendChild(tdHome);
        tr.appendChild(tdVs);
        tr.appendChild(tdAway);
        tr.appendChild(tdScore);
        scheduleBodyEl.appendChild(tr);
    });
}


// --- TEAM CREATION ANIMATION ---
function runTeamAnimation(data) {
    isAnimating = true;
    const playerMap = {};
    data.players.forEach(p => { playerMap[p.id] = p.name; });

    // For large groups, skip per-card animation
    if (data.players.length > BATCH_ANIMATION_THRESHOLD) {
        runBatchAnimation(data, playerMap);
        return;
    }

    // Show animation phase
    showPhase(phaseAnimation);
    animPlayerList.innerHTML = '';
    animTeamColumns.innerHTML = '';
    animTeamColumns.classList.remove('visible');

    // Create player cards in the animation area
    const cards = data.players.map(player => {
        const card = document.createElement('div');
        card.className = 'anim-card';
        card.textContent = player.name;
        card.dataset.playerId = player.id;
        animPlayerList.appendChild(card);
        return card;
    });

    // Phase 1: Shuffle animation
    let shuffleCount = 0;
    const shuffleInterval = setInterval(() => {
        cards.forEach(card => {
            const offsetY = (Math.random() - 0.5) * 60;
            const offsetX = (Math.random() - 0.5) * 30;
            card.style.transform = `translate(${offsetX}px, ${offsetY}px)`;
        });
        shuffleCount++;
        if (shuffleCount >= SHUFFLE_ITERATIONS) {
            clearInterval(shuffleInterval);
            // Reset positions
            cards.forEach(card => { card.style.transform = ''; });

            // Phase 2: Build team columns and distribute
            setTimeout(() => distributeCards(data, playerMap, cards), 600);
        }
    }, SHUFFLE_INTERVAL_MS);
}

function distributeCards(data, playerMap, cards) {
    // Build team columns
    animTeamColumns.innerHTML = '';
    const teamCols = data.teams.map(team => {
        const col = document.createElement('div');
        col.className = 'anim-team-col';

        const header = document.createElement('div');
        header.className = 'anim-team-header';
        header.style.background = team.color;
        header.textContent = team.name;

        const body = document.createElement('div');
        body.className = 'anim-team-body';

        col.appendChild(header);
        col.appendChild(body);
        animTeamColumns.appendChild(col);
        return { team, body };
    });

    animTeamColumns.classList.add('visible');

    // Build assignment map: playerId -> team index
    const assignment = {};
    data.teams.forEach((team, teamIdx) => {
        team.players.forEach(pid => { assignment[pid] = teamIdx; });
    });

    // Fade out cards one by one and add to team columns
    let delay = 800; // initial delay after columns appear
    cards.forEach((card, i) => {
        const pid = card.dataset.playerId;
        const teamIdx = assignment[pid];
        if (teamIdx === undefined) return;

        setTimeout(() => {
            card.style.opacity = '0';
            card.style.transform = 'scale(0.8)';

            setTimeout(() => {
                const playerEl = document.createElement('div');
                playerEl.className = 'anim-team-player';
                playerEl.textContent = playerMap[pid] || pid;
                teamCols[teamIdx].body.appendChild(playerEl);
            }, 200);
        }, delay + i * ANIMATION_STAGGER_MS);
    });

    // After all cards distributed, transition to teams phase
    const totalTime = delay + cards.length * ANIMATION_STAGGER_MS + 800;
    setTimeout(() => {
        isAnimating = false;
        renderPhase(currentState);
    }, totalTime);
}

function runBatchAnimation(data, playerMap) {
    // Simple fade: hide registration, show teams
    phaseRegistration.style.opacity = '0';
    phaseRegistration.style.transition = 'opacity 0.5s ease';

    setTimeout(() => {
        isAnimating = false;
        renderPhase(data);
        phaseRegistration.style.opacity = '';
        phaseRegistration.style.transition = '';
    }, 600);
}

})();
