const canvas = document.getElementById('wheelCanvas');
const ctx = canvas.getContext('2d');
const spinBtn = document.getElementById('spinBtn');
const indicator = document.getElementById('indicator');
const winnerTextDisplay = document.getElementById('winner-text');

// The 15-Color Club Palette (Red/White/Silver/Dark Theme)
const colorPalette = [
    '#E30613', // 1. Main Club Red
    '#FFFFFF', // 2. Pure White
    '#8A0000', // 3. Dark Maroon
    '#BDC3C7', // 4. Silver (Kit detail)
    '#C0392B', // 5. Strong Red
    '#ECF0F1', // 6. Cloud White
    '#7B241C', // 7. Deep Crimson
    '#95A5A6', // 8. Dark Grey (Away kit vibes)
    '#FF4D4D', // 9. Bright Scarlet
    '#F4F6F7', // 10. Pearl White
    '#641E16', // 11. Very Dark Red
    '#D0D3D4', // 12. Light Grey
    '#E74C3C', // 13. Alizarin Red
    '#2C3E50', // 14. Midnight Blue/Dark Grey (Contrast)
    '#FDEDEC'  // 15. Pale Red Tint
];

// Stages & Elements
const wheelStage = document.getElementById('wheel-stage');
const timerStage = document.getElementById('timer-stage');
const floatingImg = document.getElementById('floating-img');
const timerDisplay = document.getElementById('timer-display');
const timerContent = document.querySelector('.timer-content');

let sectors = [];
let currentRotation = 0;
let timerInterval = null;

// 1. Load Data
fetch('/api/get_wheel_data')
    .then(res => res.json())
    .then(data => {
        if(data.length === 0) return;

        // Preload
        const loadPromises = data.map(item => {
            return new Promise((resolve) => {
                const img = new Image();
                img.src = '/static/' + item.path;

                // We resolve with the FULL item object (path + text)
                img.onload = () => resolve({
                    imgObject: img,
                    src: '/static/' + item.path,
                    text: item.text  // <--- Store the text here
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
            text: item.text, // <--- Pass text to sector
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

        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.arc(centerX, centerY, radius, startAngle, endAngle);
        ctx.fillStyle = sector.color;
        ctx.fill();

        // VISUAL TWEAK:
        // If the sector is White (#FFFFFF), we need a darker border to see it.
        // If it's dark, a white border looks better.
        // For simplicity, we use a semi-transparent dark stroke for all.
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(0,0,0,0.2)";
        ctx.stroke();

        ctx.save();
        ctx.translate(centerX, centerY);
        ctx.rotate(angleMiddle);
        ctx.translate(radius * 0.65, 0);

        // Image drawing logic
        const imgSize = 70;

        // Create a white circle behind the image so it pops against red/dark backgrounds
        ctx.beginPath();
        ctx.arc(0, 0, imgSize/2 + 4, 0, Math.PI * 2);
        ctx.fillStyle = "white";
        ctx.fill();

        // Clip and draw image
        ctx.beginPath();
        ctx.arc(0, 0, imgSize/2, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(sector.imgObject, -imgSize/2, -imgSize/2, imgSize, imgSize);

        ctx.restore();
    });
}
// 2. Spin Logic
spinBtn.addEventListener('click', () => {
    if(sectors.length === 0) return;
    spinBtn.disabled = true;

    // Reset any previous animations
    resetVisualsForSpin();

    const spinAmount = 1800 + Math.random() * 360;
    currentRotation += spinAmount;
    canvas.style.transform = `rotate(-${currentRotation}deg)`;

    setTimeout(() => {
        calculateWinner();
    }, 5000);
});

function calculateWinner() {
    const numSectors = sectors.length;
    const degreesPerSector = 360 / numSectors;
    const actualRotation = currentRotation % 360;

    // Determine which sector is at 270deg (Top)
    let winningAngle = (270 - actualRotation) % 360;
    if (winningAngle < 0) winningAngle += 360;

    const winningIndex = Math.floor(winningAngle / degreesPerSector);
    const winner = sectors[winningIndex];

    startWinAnimation(winner);
}

// 3. Complex Animation Sequence
function startWinAnimation(winner) {
    // 1. Get coordinates of the Red Triangle (Indicator)
    const rect = indicator.getBoundingClientRect();

    // 2. Prepare the Image (Set Source & Start Position)
    floatingImg.src = winner.src;

    // Reset classes: ensure it's visible, but NO animation class yet
    floatingImg.className = ''; // Wipes 'hidden', 'motion-active', 'state-centered'

    // Manually set the start position (Top of wheel)
    // rect.left is the left edge of the triangle.
    // The triangle is 50px wide. Center is +25px.
    // The image is 80px wide. Center is -40px.
    floatingImg.style.width = '80px';
    floatingImg.style.height = '80px';
    floatingImg.style.top = (rect.top + 30) + 'px';
    floatingImg.style.left = (rect.left + 25 - 40) + 'px';
    floatingImg.style.transform = 'translate(0, 0)';

    // 3. Force Reflow
    // This tells the browser: "Paint the image at the top indicator NOW"
    void floatingImg.offsetWidth;

    // 4. Activate Animation & Set Target
    // Now we add the transition class, then immediately set the destination
    floatingImg.classList.add('motion-active'); // Turn on the engine
    floatingImg.classList.add('state-centered'); // Set the destination

    // Set text content (hidden initially)
    winnerTextDisplay.textContent = winner.text || "WINNER!";
    winnerTextDisplay.classList.remove('show');

    // 5. Fade out Wheel
    setTimeout(() => {
        wheelStage.classList.add('hidden');
    }, 500);

    // 6. Move to Top Half & Show Timer/Text
    setTimeout(() => {
        // Remove centered state, add top state
        floatingImg.classList.remove('state-centered');
        floatingImg.classList.add('state-top');

        // Show Timer Stage
        timerStage.classList.remove('hidden');

        // Fade in text
        winnerTextDisplay.classList.add('show');

        runTimer();

    }, 2500);
}

// 4. Timer Logic
function runTimer() {
    let timeLeft = 10; // 60 Seconds
    updateTimerUI(timeLeft);

    // Clear existing if any
    if(timerInterval) clearInterval(timerInterval);

    timerInterval = setInterval(() => {
        timeLeft--;
        updateTimerUI(timeLeft);

        // Pulse Effect at 5 seconds
        if (timeLeft <= 5 && timeLeft > 0) {
            timerContent.classList.add('pulse-red');
        }

        // Finish
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

function resetVisualsForSpin() {
    // Helper to ensure clean state before spinning
    timerContent.classList.remove('pulse-red');
}

// 5. Full Reset
function resetApp() {
    // Stop pulsing
    timerContent.classList.remove('pulse-red');

    // Hide Timer & Text
    timerStage.classList.add('hidden');
    winnerTextDisplay.classList.remove('show');

    // Hide Floating Image & Reset Classes
    floatingImg.classList.add('hidden');
    floatingImg.classList.remove('state-top', 'state-centered', 'motion-active'); // <--- Removed motion-active

    // Show Wheel Stage
    wheelStage.classList.remove('hidden');

    spinBtn.disabled = false;
    timerDisplay.textContent = "01:00";
}