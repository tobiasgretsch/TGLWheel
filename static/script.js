// --- DOM ELEMENTS ---
const canvas = document.getElementById('wheelCanvas');
const ctx = canvas.getContext('2d');
const indicator = document.getElementById('indicator');

// Stages
const wheelStage = document.getElementById('wheel-stage');
const timerStage = document.getElementById('timer-stage');
const winnerTextDisplay = document.getElementById('winner-text');

// Floating Image
const floatingImg = document.getElementById('floating-img');

// Timer Elements
const timerDisplay = document.getElementById('timer-display');
const timerContent = document.querySelector('.timer-content');

// --- VARIABLES ---
let sectors = [];
let currentRotation = 0;
let timerInterval = null;
let lastCommandId = 0; // For remote control

// --- CONFIGURATION ---
const colorPalette = [
    '#E30613', '#FFFFFF', '#8A0000', '#BDC3C7', '#C0392B',
    '#ECF0F1', '#7B241C', '#95A5A6', '#FF4D4D', '#F4F6F7',
    '#641E16', '#D0D3D4', '#E74C3C', '#2C3E50', '#FDEDEC'
];

// --- 1. INITIALIZATION & DATA LOADING ---
// Start Polling immediately
setInterval(checkForCommands, 500);

fetch('/api/get_wheel_data')
    .then(res => res.json())
    .then(data => {
        if(data.length === 0) return;

        const loadPromises = data.map(item => {
            return new Promise((resolve) => {
                const img = new Image();
                img.src = '/static/' + item.path;
                img.onload = () => resolve({
                    imgObject: img,
                    src: '/static/' + item.path,
                    text: item.text
                });
                img.onerror = () => resolve(null);
            });
        });

        Promise.all(loadPromises).then(loaded => {
            const valid = loaded.filter(i => i !== null);
            initWheel(valid);
        });
    });

function initWheel(items) {
    const numSectors = items.length;
    const arcSize = (2 * Math.PI) / numSectors;

    sectors = items.map((item, i) => {
        return {
            imgObject: item.imgObject,
            src: item.src,
            text: item.text,
            color: colorPalette[i % colorPalette.length],
            startAngle: i * arcSize,
            endAngle: (i + 1) * arcSize
        };
    });
    drawWheel();
}

function drawWheel() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const radius = canvas.width / 2;

    sectors.forEach((sector) => {
        const startAngle = sector.startAngle;
        const endAngle = sector.endAngle;
        const angleMiddle = startAngle + (endAngle - startAngle) / 2;

        // Sector
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.arc(centerX, centerY, radius, startAngle, endAngle);
        ctx.fillStyle = sector.color;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(0,0,0,0.2)";
        ctx.stroke();

        // Image
        ctx.save();
        ctx.translate(centerX, centerY);
        ctx.rotate(angleMiddle);
        ctx.translate(radius * 0.65, 0);

        const imgSize = 70;

        // White Background for Image
        ctx.beginPath();
        ctx.arc(0, 0, imgSize/2 + 4, 0, Math.PI * 2);
        ctx.fillStyle = "white";
        ctx.fill();

        // Image itself
        ctx.beginPath();
        ctx.arc(0, 0, imgSize/2, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(sector.imgObject, -imgSize/2, -imgSize/2, imgSize, imgSize);
        ctx.restore();
    });
}

// --- 2. REMOTE CONTROL POLLING ---
function checkForCommands() {
    fetch('/api/check_status')
        .then(res => res.json())
        .then(data => {
            // Check if there is a NEW command (ID is different from last one)
            if (data.command_id !== 0 && data.command_id !== lastCommandId) {
                console.log("New Command Received:", data.command);

                lastCommandId = data.command_id;

                if (data.command === 'spin') {
                    startSpinSequence();
                } else if (data.command === 'reset') {
                    resetApp();
                }
            }
        })
        .catch(err => console.error("Polling Error:", err));
}

// --- 3. ANIMATION LOGIC ---
function startSpinSequence() {
    // Safety Checks: Do not spin if empty or already busy (wheel hidden)
    if(sectors.length === 0) {
        console.warn("Cannot spin: No sectors loaded.");
        return;
    }
    if(wheelStage.classList.contains('hidden')) {
        console.warn("Cannot spin: Wheel is hidden (Game in progress).");
        return;
    }

    console.log("Starting Spin...");

    // Reset any pulse effects
    timerContent.classList.remove('pulse-red');

    // Calculate rotation
    const spinAmount = 1800 + Math.random() * 360;
    currentRotation += spinAmount;
    canvas.style.transform = `rotate(-${currentRotation}deg)`;

    // Wait 5 seconds for CSS animation
    setTimeout(() => {
        calculateWinner();
    }, 5000);
}

function calculateWinner() {
    const numSectors = sectors.length;
    const degreesPerSector = 360 / numSectors;
    const actualRotation = currentRotation % 360;

    let winningAngle = (270 - actualRotation) % 360;
    if (winningAngle < 0) winningAngle += 360;

    const winningIndex = Math.floor(winningAngle / degreesPerSector);
    const winner = sectors[winningIndex];

    console.log("Winner determined:", winner.text);
    startWinAnimation(winner);
}

function startWinAnimation(winner) {
    const rect = indicator.getBoundingClientRect();

    // 1. Position Image at Indicator (No Animation)
    floatingImg.src = winner.src;
    floatingImg.className = ''; // Reset classes

    floatingImg.style.width = '80px';
    floatingImg.style.height = '80px';
    floatingImg.style.top = (rect.top + 30) + 'px';
    floatingImg.style.left = (rect.left + 25 - 40) + 'px';
    floatingImg.style.transform = 'translate(0, 0)';

    // Force Reflow
    void floatingImg.offsetWidth;

    // 2. Animate to Center
    floatingImg.classList.add('motion-active');
    floatingImg.classList.add('state-centered');

    // Set Text
    winnerTextDisplay.textContent = winner.text || "WINNER";
    winnerTextDisplay.classList.remove('show');

    // 3. Hide Wheel
    setTimeout(() => {
        wheelStage.classList.add('hidden');
    }, 500);

    // 4. Move to Top & Show Result
    setTimeout(() => {
        floatingImg.classList.remove('state-centered');
        floatingImg.classList.add('state-top');

        timerStage.classList.remove('hidden');
        winnerTextDisplay.classList.add('show');

        runTimer();
    }, 2500);
}

// --- 4. TIMER LOGIC ---
function runTimer() {
    let timeLeft = 60; // 60 Seconds
    updateTimerUI(timeLeft);

    if(timerInterval) clearInterval(timerInterval);

    timerInterval = setInterval(() => {
        timeLeft--;
        updateTimerUI(timeLeft);

        if (timeLeft <= 5 && timeLeft > 0) {
            timerContent.classList.add('pulse-red');
        }

        if (timeLeft <= 0) {
            clearInterval(timerInterval);
            resetApp();
        }
    }, 1000);
}

function updateTimerUI(seconds) {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = (seconds % 60).toString().padStart(2, '0');
    timerDisplay.textContent = `${m}:${s}`;
}

// --- 5. RESET LOGIC ---
function resetApp() {
    console.log("Resetting App...");
    if(timerInterval) clearInterval(timerInterval);

    timerContent.classList.remove('pulse-red');
    timerStage.classList.add('hidden');
    winnerTextDisplay.classList.remove('show');

    floatingImg.classList.add('hidden');
    floatingImg.classList.remove('state-top', 'state-centered', 'motion-active');

    wheelStage.classList.remove('hidden');

    timerDisplay.textContent = "01:00";
}