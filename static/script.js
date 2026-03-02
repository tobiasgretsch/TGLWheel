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

// Config State (synced from server)
let appConfig = {
    result_duration: 60,
    global_time_remaining: 600,
    global_timer_running: false,
};

// --- CONFIGURATION ---
const colorPalette = [
    '#E30613', '#FFFFFF', '#8A0000', '#BDC3C7', '#C0392B',
    '#ECF0F1', '#7B241C', '#95A5A6', '#FF4D4D', '#F4F6F7',
    '#641E16', '#D0D3D4', '#E74C3C', '#2C3E50', '#FDEDEC'
];

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
        color: colorPalette[i % colorPalette.length],
        startAngle: i * arcSize,
        endAngle: (i + 1) * arcSize,
    }));
    drawWheel();
}

function drawWheel() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const radius = canvas.width / 2;
    const IMAGE_SIZE = 70;
    const IMAGE_RADIUS = IMAGE_SIZE / 2;
    const IMAGE_DIST = radius * 0.65;

    sectors.forEach(sector => {
        const mid = sector.startAngle + (sector.endAngle - sector.startAngle) / 2;

        // Draw sector slice
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.arc(centerX, centerY, radius, sector.startAngle, sector.endAngle);
        ctx.fillStyle = sector.color;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = 'rgba(0,0,0,0.2)';
        ctx.stroke();

        // Draw thumbnail image in a circular clip
        ctx.save();
        ctx.translate(centerX, centerY);
        ctx.rotate(mid);
        ctx.translate(IMAGE_DIST, 0);
        ctx.beginPath();
        ctx.arc(0, 0, IMAGE_RADIUS + 4, 0, Math.PI * 2);
        ctx.fillStyle = 'white';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(0, 0, IMAGE_RADIUS, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(sector.imgObject, -IMAGE_RADIUS, -IMAGE_RADIUS, IMAGE_SIZE, IMAGE_SIZE);
        ctx.restore();
    });
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

    setTimeout(() => { wheelStage.classList.add('hidden'); }, 500);

    setTimeout(() => {
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
    // Cancel any in-flight spin so the winner reveal never fires after a reset.
    if (spinTimeoutId) {
        clearTimeout(spinTimeoutId);
        spinTimeoutId = null;
    }

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
    floatingImg.classList.add('hidden');
    floatingImg.classList.remove('state-top', 'state-centered', 'motion-active');
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
