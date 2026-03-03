// --- CONSTANTS ---
// SPIN_DURATION_MS is read directly from the CSS transition on the canvas so it can
// never silently diverge from --spin-duration in style.css.
const SPIN_DURATION_MS = parseFloat(getComputedStyle(document.getElementById('wheelCanvas')).transitionDuration) * 1000;
const MIN_SPIN_ROTATIONS = 5;   // Full rotations before the random stop angle
const RESULT_TICK_MS = 200;     // Result timer checks every 200ms to stay accurate

// --- DOM ELEMENTS ---
const canvas = document.getElementById('wheelCanvas');
const ctx = canvas.getContext('2d');
const indicator = document.getElementById('indicator');
const wheelStage = document.getElementById('wheel-stage');
const timerStage = document.getElementById('timer-stage');
const winnerTextDisplay = document.getElementById('winner-text');
const floatingImg = document.getElementById('floating-img');
const timerDisplay = document.getElementById('timer-display');
const timerContent = document.querySelector('.timer-content');
const globalTimerEl = document.getElementById('global-timer');
const scoreLeftEl = document.getElementById('score-val-left');
const scoreRightEl = document.getElementById('score-val-right');

// --- STATE ---
let sectors = [];
let currentRotation = 0;
let isSpinning = false;
let lastCommandId = 0;
let resultTimerInterval = null;
let resultTimerEndMs = null;
let resultTimerRemaining = 0;          // seconds frozen when paused
let resultTimerState = 'idle';         // 'idle' | 'ready' | 'running' | 'paused'
let globalTimerEndMs = null;           // Date.now() + remaining_ms when the game clock is running
let spinTimeoutId = null;              // setTimeout handle for the post-spin winner reveal
let winAnimTimeoutA = null;            // 500ms: hide wheel stage after win
let winAnimTimeoutB = null;            // 2500ms: show timer stage / winner text

// Config State (synced from server)
let appConfig = {
    result_duration: 60,
    global_time_remaining: 600,
    global_timer_running: false,
};

// --- CONFIGURATION ---
// Two alternating sector colours — deep red / dark navy — match the UI tokens.
const SECTOR_COLORS = ['#B03030', '#1C3455'];

// --- 1. INITIALIZATION ---
// SSE: The server pushes state changes instantly instead of the client polling.
// The first message sent on connect carries the full current state, which initialises
// lastCommandId and prevents stale commands from replaying after a page refresh.
const eventSource = new EventSource('/api/stream');
eventSource.onmessage = (event) => {
    handleStateUpdate(JSON.parse(event.data));
};
// EventSource reconnects automatically on error — no manual handling needed.

fetch('/api/get_wheel_data')
    .then(res => res.json())
    .then(data => {
        if (data.length === 0) return;
        const loadPromises = data.map(item => new Promise(resolve => {
            const img = new Image();
            img.src = '/static/' + item.path;
            img.onload = () => resolve({ imgObject: img, src: item.path, text: item.text });
            img.onerror = () => resolve(null);
        }));
        Promise.all(loadPromises).then(loaded => {
            initWheel(loaded.filter(i => i !== null));
        });
    });

// Global timer display tick. The source of truth is globalTimerEndMs (set from server
// data), so this interval only drives the UI and does not accumulate drift.
setInterval(() => {
    if (globalTimerEndMs === null) return;
    const remaining = Math.max(0, Math.ceil((globalTimerEndMs - Date.now()) / 1000));
    updateGlobalTimerUI(remaining);
    if (remaining === 0) globalTimerEndMs = null;
}, 1000);


function initWheel(items) {
    const arcSize = (2 * Math.PI) / items.length;
    sectors = items.map((item, i) => ({
        imgObject: item.imgObject,
        src: '/static/' + item.src,
        text: item.text,
        startAngle: i * arcSize,
        endAngle: (i + 1) * arcSize,
    }));
    drawWheel();
}

function drawWheel() {
    const W = canvas.width;
    const cx = W / 2;
    const cy = W / 2;
    const outerR = W / 2;        // 300
    const RIM_W = 22;
    const discR = outerR - RIM_W; // 278
    const IMG_SIZE = 82;
    const IMG_RADIUS = IMG_SIZE / 2;
    const IMG_DIST = discR * 0.65; // ~181

    ctx.clearRect(0, 0, W, W);

    // Layer 1: Outer decorative rim
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
    ctx.fillStyle = '#0d1520';
    ctx.fill();

    // Layer 2: Sector fills up to discR
    sectors.forEach((sector, i) => {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, discR, sector.startAngle, sector.endAngle);
        ctx.closePath();
        ctx.fillStyle = SECTOR_COLORS[i % SECTOR_COLORS.length];
        ctx.fill();
    });

    // Layer 3: Sector divider lines from centre to outer rim
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    sectors.forEach(sector => {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        const x = cx + outerR * Math.cos(sector.startAngle);
        const y = cy + outerR * Math.sin(sector.startAngle);
        ctx.lineTo(x, y);
        ctx.stroke();
    });

    // Layer 4: Circular images with white border ring
    sectors.forEach(sector => {
        const mid = sector.startAngle + (sector.endAngle - sector.startAngle) / 2;

        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(mid);
        ctx.translate(IMG_DIST, 0);

        // White border ring drawn outside clip
        ctx.beginPath();
        ctx.arc(0, 0, IMG_RADIUS + 2, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.fill();

        // Clip and draw image (cover-scale so shorter side fills the circle)
        ctx.beginPath();
        ctx.arc(0, 0, IMG_RADIUS, 0, Math.PI * 2);
        ctx.clip();
        const iw = sector.imgObject.naturalWidth;
        const ih = sector.imgObject.naturalHeight;
        const scale = Math.max(IMG_SIZE / iw, IMG_SIZE / ih);
        const dw = iw * scale;
        const dh = ih * scale;
        ctx.drawImage(sector.imgObject, -dw / 2, -dh / 2, dw, dh);
        ctx.restore();
    });

    // Layer 5: Rim tick marks at each sector boundary inside the rim band
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = 'rgba(255,255,255,0.4)';
    sectors.forEach(sector => {
        const angle = sector.startAngle;
        ctx.beginPath();
        ctx.moveTo(cx + discR * Math.cos(angle), cy + discR * Math.sin(angle));
        ctx.lineTo(cx + outerR * Math.cos(angle), cy + outerR * Math.sin(angle));
        ctx.stroke();
    });

    // Layer 6: Rim dots — one per sector centred on the arc in the rim band
    const dotR = (discR + outerR) / 2;
    sectors.forEach(sector => {
        const mid = sector.startAngle + (sector.endAngle - sector.startAngle) / 2;
        ctx.beginPath();
        ctx.arc(cx + dotR * Math.cos(mid), cy + dotR * Math.sin(mid), 3, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255,255,255,0.28)';
        ctx.fill();
    });

    // Layer 7: Edge rings on inner and outer rim edge
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255,255,255,0.12)';
    ctx.beginPath();
    ctx.arc(cx, cy, discR, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, outerR - 1, 0, Math.PI * 2);
    ctx.stroke();

    // Layer 8: Centre backing disc (sits under the HTML hub element)
    ctx.beginPath();
    ctx.arc(cx, cy, W * 0.13, 0, Math.PI * 2);
    ctx.fillStyle = '#1a2332';
    ctx.fill();
}


// --- 2. STATE HANDLER ---
function handleStateUpdate(data) {
    // Update score display only when the value actually changed.
    const leftVal = String(data.scores.left);
    const rightVal = String(data.scores.right);
    if (scoreLeftEl.textContent !== leftVal) scoreLeftEl.textContent = leftVal;
    if (scoreRightEl.textContent !== rightVal) scoreRightEl.textContent = rightVal;

    syncConfigFromState(data);

    if (data.command_id !== 0 && data.command_id !== lastCommandId) {
        lastCommandId = data.command_id;
        if (data.command === 'spin') {
            startSpinSequence();
        } else if (data.command === 'reset') {
            resetApp();
        }
    }
}


// --- 3. CONFIG SYNC ---
function syncConfigFromState(data) {
    if (!data.config) return;

    // Capture previous running state BEFORE overwriting so we can detect transitions.
    const prevRunning = appConfig.global_timer_running;
    const nextRunning = data.config.global_timer_running;

    appConfig.result_duration = data.config.result_duration;
    appConfig.global_time_remaining = data.config.global_time_remaining;
    appConfig.global_timer_running = nextRunning;

    if (nextRunning) {
        // Anchor the local end time to now + server-reported remaining so the local
        // display tick stays in sync with the server.
        globalTimerEndMs = Date.now() + data.config.global_time_remaining * 1000;
    } else {
        globalTimerEndMs = null;
        updateGlobalTimerUI(data.config.global_time_remaining);
    }

    if (data.config.global_timer_size !== undefined) {
        globalTimerEl.style.fontSize = data.config.global_timer_size + 'rem';
    }

    // Link the result timer to the global game timer.
    if (!prevRunning && nextRunning) {
        // Global timer just STARTED → also start/resume the result timer if it is waiting.
        if (resultTimerState === 'ready' || resultTimerState === 'paused') {
            startResultTimer();
        }
    } else if (prevRunning && !nextRunning) {
        // Global timer just PAUSED → also pause the result timer if it is running.
        if (resultTimerState === 'running') {
            pauseResultTimer();
        }
    }
}


// --- 4. GLOBAL TIMER UI ---
function updateGlobalTimerUI(totalSec) {
    const m = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const s = Math.floor(totalSec % 60).toString().padStart(2, '0');
    globalTimerEl.textContent = `${m}:${s}`;
}


// --- 5. SPIN LOGIC ---
function startSpinSequence() {
    if (sectors.length === 0 || isSpinning || wheelStage.classList.contains('hidden')) return;
    isSpinning = true;
    timerContent.classList.remove('pulse-red');

    // Pre-select the winning sector so the rotation distance is always exactly
    // MIN_SPIN_ROTATIONS * 360 + sector_offset degrees — consistent speed every time.
    const degreesPerSector = 360 / sectors.length;
    const winningIndex = Math.floor(Math.random() * sectors.length);

    // Indicator is at the top (270°). Reverse the calculateWinner formula to find
    // the rotation that places the winning sector's midpoint under the indicator.
    const targetAngle = (winningIndex + 0.5) * degreesPerSector;
    const neededRotation = ((targetAngle - 270) + 360) % 360;
    const spinAmount = MIN_SPIN_ROTATIONS * 360 + neededRotation;

    currentRotation += spinAmount;
    canvas.style.transform = `rotate(-${currentRotation}deg)`;

    // Delay must match --spin-duration in style.css (read via SPIN_DURATION_MS above).
    // Store the ID so resetApp() can cancel it if RESET is pressed mid-spin.
    spinTimeoutId = setTimeout(() => {
        spinTimeoutId = null;
        startWinAnimation(sectors[winningIndex]);
    }, SPIN_DURATION_MS);
}

function startWinAnimation(winner) {
    const rect = indicator.getBoundingClientRect();
    floatingImg.src = winner.src;
    floatingImg.className = '';
    floatingImg.style.width = '80px';
    floatingImg.style.height = '80px';
    floatingImg.style.top = `${rect.top + 30}px`;
    floatingImg.style.left = `${rect.left - 15}px`; // Centre on indicator: half image width (40) minus indicator offset (25)
    floatingImg.style.transform = 'translate(0, 0)';

    // Force a reflow to commit the starting position before adding the animation class.
    // Without this the browser batches the writes and the CSS transition does not fire.
    void floatingImg.offsetWidth;

    floatingImg.classList.add('motion-active', 'state-centered');
    winnerTextDisplay.textContent = winner.text || 'WINNER';
    winnerTextDisplay.classList.remove('show');

    winAnimTimeoutA = setTimeout(() => {
        winAnimTimeoutA = null;
        wheelStage.classList.add('hidden');
    }, 500);

    winAnimTimeoutB = setTimeout(() => {
        winAnimTimeoutB = null;
        floatingImg.classList.remove('state-centered');
        floatingImg.classList.add('state-top');
        timerStage.classList.remove('hidden');
        winnerTextDisplay.classList.add('show');
        armResultTimer();
    }, 2500);
}


// --- 6. RESULT TIMER ---

// Called when the result screen appears. Displays the full duration but does NOT
// start counting — waits for the global game timer to be started via START.
function armResultTimer() {
    resultTimerState = 'ready';
    resultTimerRemaining = appConfig.result_duration;
    updateResultTimerUI(resultTimerRemaining);
}

// Starts (or resumes) the result timer countdown. Picks up from the frozen
// remaining time if paused, otherwise starts from the full configured duration.
function startResultTimer() {
    if (resultTimerInterval) clearInterval(resultTimerInterval);
    const fromSeconds = resultTimerState === 'paused' ? resultTimerRemaining : appConfig.result_duration;
    resultTimerState = 'running';
    resultTimerEndMs = Date.now() + fromSeconds * 1000;
    updateResultTimerUI(fromSeconds);

    // Tick faster than 1s so the displayed second is always accurate.
    resultTimerInterval = setInterval(() => {
        const remaining = Math.ceil((resultTimerEndMs - Date.now()) / 1000);
        updateResultTimerUI(remaining);

        if (remaining <= 5 && remaining > 0) {
            timerContent.classList.add('pulse-red');
        }
        if (remaining <= 0) {
            clearInterval(resultTimerInterval);
            resultTimerInterval = null;
            resultTimerState = 'idle';
            resetApp();
        }
    }, RESULT_TICK_MS);
}

// Freezes the result timer at its current remaining value.
function pauseResultTimer() {
    if (resultTimerInterval) {
        clearInterval(resultTimerInterval);
        resultTimerInterval = null;
    }
    resultTimerRemaining = Math.max(0, Math.ceil((resultTimerEndMs - Date.now()) / 1000));
    resultTimerState = 'paused';
    timerContent.classList.remove('pulse-red');
    updateResultTimerUI(resultTimerRemaining);
}

function updateResultTimerUI(seconds) {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    timerDisplay.textContent = `${m}:${s}`;
}


// --- 7. RESET ---
function resetApp() {
    // Cancel all in-flight timeouts so no deferred animation step can fire after reset.
    if (spinTimeoutId)   { clearTimeout(spinTimeoutId);   spinTimeoutId   = null; }
    if (winAnimTimeoutA) { clearTimeout(winAnimTimeoutA); winAnimTimeoutA = null; }
    if (winAnimTimeoutB) { clearTimeout(winAnimTimeoutB); winAnimTimeoutB = null; }

    if (resultTimerInterval) {
        clearInterval(resultTimerInterval);
        resultTimerInterval = null;
    }
    resultTimerState = 'idle';
    resultTimerRemaining = 0;
    isSpinning = false;
    timerContent.classList.remove('pulse-red');
    timerStage.classList.add('hidden');
    winnerTextDisplay.classList.remove('show');
    winnerTextDisplay.textContent = '';

    // Remove motion-active FIRST to kill any running CSS transition, then force a
    // reflow so the browser commits the stopped state before display:none is applied.
    // Without this, transition:all can keep the image painted for another frame.
    floatingImg.classList.remove('motion-active');
    void floatingImg.offsetWidth;
    floatingImg.classList.remove('state-top', 'state-centered');
    floatingImg.className = 'hidden';
    floatingImg.style.cssText = '';     // wipe inline top/left/width/height set by startWinAnimation

    wheelStage.classList.remove('hidden');
    updateResultTimerUI(appConfig.result_duration);

    // Snap the canvas back to 0° instantly (disabling the transition so the audience
    // does not see a 5-second reverse spin). currentRotation is reset so the next
    // spin always travels the same fixed distance from a clean starting point.
    canvas.style.transition = 'none';
    canvas.style.transform = 'rotate(0deg)';
    currentRotation = 0;
    void canvas.offsetWidth; // commit the style before re-enabling transition
    canvas.style.transition = '';
}
