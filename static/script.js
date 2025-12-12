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

// New DOM Elements
const globalTimerEl = document.getElementById('global-timer');
const scoreLeftEl = document.getElementById('score-val-left');
const scoreRightEl = document.getElementById('score-val-right');

// --- VARIABLES ---
let sectors = [];
let currentRotation = 0;
let resultTimerInterval = null;
let lastCommandId = 0;

// Config State (synced from server)
let appConfig = {
    result_duration: 60,
    global_time_remaining: 600,
    global_timer_running: false
};

// Global Timer Interval
let globalTimerInterval = setInterval(tickGlobalTimer, 1000);

// --- CONFIGURATION ---
const colorPalette = [
    '#E30613', '#FFFFFF', '#8A0000', '#BDC3C7', '#C0392B',
    '#ECF0F1', '#7B241C', '#95A5A6', '#FF4D4D', '#F4F6F7',
    '#641E16', '#D0D3D4', '#E74C3C', '#2C3E50', '#FDEDEC'
];

// --- 1. INITIALIZATION ---
// Poll fast for smooth UI updates
setInterval(checkForCommands, 500);

fetch('/api/get_wheel_data')
    .then(res => res.json())
    .then(data => {
        if(data.length === 0) return;
        const loadPromises = data.map(item => {
            return new Promise((resolve) => {
                const img = new Image();
                img.src = '/static/' + item.path;
                img.onload = () => resolve({ imgObject: img, src: item.path, text: item.text }); // src fixed
                img.onerror = () => resolve(null);
            });
        });
        Promise.all(loadPromises).then(loaded => {
            initWheel(loaded.filter(i => i !== null));
        });
    });

function initWheel(items) {
    const numSectors = items.length;
    const arcSize = (2 * Math.PI) / numSectors;
    sectors = items.map((item, i) => {
        return {
            imgObject: item.imgObject,
            src: '/static/' + item.src,
            text: item.text,
            color: colorPalette[i % colorPalette.length],
            startAngle: i * arcSize,
            endAngle: (i + 1) * arcSize
        };
    });
    drawWheel();
}

// ... drawWheel function remains exactly the same as before ...
function drawWheel() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const radius = canvas.width / 2;
    sectors.forEach((sector) => {
        const startAngle = sector.startAngle;
        const endAngle = sector.endAngle;
        const angleMiddle = startAngle + (endAngle - startAngle) / 2;
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.arc(centerX, centerY, radius, startAngle, endAngle);
        ctx.fillStyle = sector.color;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(0,0,0,0.2)";
        ctx.stroke();
        ctx.save();
        ctx.translate(centerX, centerY);
        ctx.rotate(angleMiddle);
        ctx.translate(radius * 0.65, 0);
        const imgSize = 70;
        ctx.beginPath();
        ctx.arc(0, 0, imgSize/2 + 4, 0, Math.PI * 2);
        ctx.fillStyle = "white";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(0, 0, imgSize/2, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(sector.imgObject, -imgSize/2, -imgSize/2, imgSize, imgSize);
        ctx.restore();
    });
}

// --- 2. POLLING & STATE MANAGEMENT ---
function checkForCommands() {
    fetch('/api/check_status')
        .then(res => res.json())
        .then(data => {
            // A. Update Scores immediately
            if(data.scores) {
                scoreLeftEl.textContent = data.scores.left;
                scoreRightEl.textContent = data.scores.right;
            }

            // B. Check for new commands
            if (data.command_id !== 0 && data.command_id !== lastCommandId) {
                lastCommandId = data.command_id;

                if (data.command === 'spin') {
                    startSpinSequence();
                } else if (data.command === 'reset') {
                    resetApp();
                } else if (data.command === 'set_timers') {
                    // Update Local Config
                    appConfig.result_duration = data.config.result_duration;
                    appConfig.global_time_remaining = data.config.global_time_set;
                    appConfig.global_timer_running = false; // Stop when setting new time
                    updateGlobalTimerUI();
                } else if (data.command === 'control_global_timer') {
                    appConfig.global_timer_running = data.config.global_timer_running;
                }
            }
        });
}

// --- 3. GLOBAL TIMER LOGIC ---
function tickGlobalTimer() {
    if (appConfig.global_timer_running && appConfig.global_time_remaining > 0) {
        appConfig.global_time_remaining--;
        updateGlobalTimerUI();
    }
}

function updateGlobalTimerUI() {
    const totalSec = appConfig.global_time_remaining;
    const m = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const s = (totalSec % 60).toString().padStart(2, '0');
    globalTimerEl.textContent = `${m}:${s}`;
}

// --- 4. ANIMATION LOGIC ---
function startSpinSequence() {
    if(sectors.length === 0 || wheelStage.classList.contains('hidden')) return;
    timerContent.classList.remove('pulse-red');
    const spinAmount = 1800 + Math.random() * 360;
    currentRotation += spinAmount;
    canvas.style.transform = `rotate(-${currentRotation}deg)`;
    setTimeout(calculateWinner, 5000);
}

function calculateWinner() {
    const numSectors = sectors.length;
    const degreesPerSector = 360 / numSectors;
    const actualRotation = currentRotation % 360;
    let winningAngle = (270 - actualRotation) % 360;
    if (winningAngle < 0) winningAngle += 360;
    const winningIndex = Math.floor(winningAngle / degreesPerSector);
    startWinAnimation(sectors[winningIndex]);
}

function startWinAnimation(winner) {
    const rect = indicator.getBoundingClientRect();
    floatingImg.src = winner.src;
    floatingImg.className = '';
    floatingImg.style.width = '80px';
    floatingImg.style.height = '80px';
    floatingImg.style.top = (rect.top + 30) + 'px';
    floatingImg.style.left = (rect.left + 25 - 40) + 'px';
    floatingImg.style.transform = 'translate(0, 0)';
    void floatingImg.offsetWidth;

    floatingImg.classList.add('motion-active');
    floatingImg.classList.add('state-centered');

    winnerTextDisplay.textContent = winner.text || "WINNER";
    winnerTextDisplay.classList.remove('show');

    setTimeout(() => { wheelStage.classList.add('hidden'); }, 500);

    setTimeout(() => {
        floatingImg.classList.remove('state-centered');
        floatingImg.classList.add('state-top');
        timerStage.classList.remove('hidden');
        winnerTextDisplay.classList.add('show');
        runResultTimer(); // Use new timer function
    }, 2500);
}

// --- 5. RESULT TIMER LOGIC ---
function runResultTimer() {
    // USE THE CONFIG DURATION
    let timeLeft = appConfig.result_duration;
    updateResultTimerUI(timeLeft);

    if(resultTimerInterval) clearInterval(resultTimerInterval);

    resultTimerInterval = setInterval(() => {
        timeLeft--;
        updateResultTimerUI(timeLeft);

        if (timeLeft <= 5 && timeLeft > 0) {
            timerContent.classList.add('pulse-red');
        }

        if (timeLeft <= 0) {
            clearInterval(resultTimerInterval);
            resetApp();
        }
    }, 1000);
}

function updateResultTimerUI(seconds) {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = (seconds % 60).toString().padStart(2, '0');
    timerDisplay.textContent = `${m}:${s}`;
}

// --- 6. RESET ---
function resetApp() {
    if(resultTimerInterval) clearInterval(resultTimerInterval);
    timerContent.classList.remove('pulse-red');
    timerStage.classList.add('hidden');
    winnerTextDisplay.classList.remove('show');
    floatingImg.classList.add('hidden');
    floatingImg.classList.remove('state-top', 'state-centered', 'motion-active');
    wheelStage.classList.remove('hidden');

    // Reset timer text to config value
    const defSec = appConfig.result_duration;
    updateResultTimerUI(defSec);
}