/* ============================================================
   Волк ловит яйца — клон классической игры Game & Watch "Egg"
   ============================================================ */

const canvas = document.getElementById('scene');
const ctx = canvas.getContext('2d');
const W = canvas.width;
const H = canvas.height;

const els = {
  score: document.getElementById('score'),
  misses: document.getElementById('misses'),
  highscore: document.getElementById('highscore'),
  start: document.getElementById('btn-start'),
  sound: document.getElementById('sound'),
  left: document.getElementById('btn-left'),
  right: document.getElementById('btn-right'),
  overlay: document.getElementById('overlay'),
  overlayTitle: document.getElementById('overlay-title'),
  overlayHint: document.getElementById('overlay-hint'),
};

const COLS = 4;
const COL_X = [75, 170, 265, 360]; // позиции волка и цели падения яиц
const CHUTE_X = [40, 360];         // левый и правый лотки
const LAND_Y = H - 150;            // линия, на которой яйцо приземляется
const WOLF_Y = H - 120;            // уровень волка
const STEPS = 14;                  // шагов от лотка до низа
const EGG_R = 8;

let state = 'title';   // title | playing | gameover
let wolfCol = 1;
let facing = 1;        // 1 = смотрит вправо, -1 = влево
let score = 0;
let misses = 0;
let highScore = 0;
let eggs = [];
let spawnInterval = 1100;
let fallInterval = 260;
let lastSpawn = 0;
let lastFall = 0;
let flash = 0;
let anim = 0;
let soundEnabled = true;
let audioCtx = null;
let rafId = null;

function loadHighScore() {
  try { highScore = parseInt(localStorage.getItem('eggGameHigh') || '0', 10) || 0; } catch (e) { highScore = 0; }
}
function saveHighScore() {
  try { localStorage.setItem('eggGameHigh', String(highScore)); } catch (e) {}
}

// ---------- звук (Web Audio, без файлов) ----------
function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audioCtx = new Ctx();
  }
  if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
}

function tone(freq, duration, type = 'square', vol = 0.1) {
  if (!soundEnabled || !audioCtx) return;
  const t = audioCtx.currentTime;
  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t);
  g.gain.setValueAtTime(vol, t);
  g.gain.exponentialRampToValueAtTime(0.001, t + duration);
  o.connect(g);
  g.connect(audioCtx.destination);
  o.start(t);
  o.stop(t + duration);
}

function sndCatch() { tone(900, 0.06); setTimeout(() => tone(1150, 0.08), 50); }
function sndMiss() { tone(140, 0.22, 'sawtooth', 0.12); }
function sndGameOver() { [200, 160, 130, 100].forEach((f, i) => setTimeout(() => tone(f, 0.4, 'sawtooth'), i * 200)); }
function sndStart() { tone(550, 0.1); setTimeout(() => tone(850, 0.18), 120); }

// ---------- игровая логика ----------
function resetGame() {
  wolfCol = 1;
  facing = 1;
  score = 0;
  misses = 0;
  eggs = [];
  flash = 0;
  anim = 0;
  spawnInterval = 1100;
  fallInterval = 260;
  const now = performance.now();
  lastSpawn = now;
  lastFall = now;
  updateHud();
  state = 'playing';
  hideOverlay();
  ensureAudio();
  sndStart();
  if (!rafId) rafId = requestAnimationFrame(loop);
}

function endGame() {
  state = 'gameover';
  if (score > highScore) { highScore = score; saveHighScore(); }
  updateHud();
  sndGameOver();
  const html = `Поймано яиц: <strong>${score}</strong><br>Рекорд: <strong>${highScore}</strong>`;
  showOverlay('Игра окончена', html, 'Сыграть ещё');
}

function spawnEgg() {
  const side = Math.random() < 0.5 ? 0 : 1;
  const target = Math.floor(Math.random() * COLS);
  const startX = CHUTE_X[side];
  const targetX = COL_X[target];
  const startY = 35;
  eggs.push({
    x: startX,
    y: startY,
    vx: (targetX - startX) / STEPS,
    vy: (LAND_Y - startY) / STEPS,
    target,
    step: 0,
  });
}

function move(dir) {
  if (state !== 'playing') return;
  wolfCol = Math.max(0, Math.min(COLS - 1, wolfCol + dir));
  if (dir !== 0) facing = dir;
}

function update(now) {
  // nuevas яйца из лотков
  if (now - lastSpawn > spawnInterval) {
    lastSpawn = now;
    spawnEgg();
    spawnInterval = Math.max(450, 1100 - score * 10);
  }

  // падение яиц на один шаг
  if (now - lastFall > fallInterval) {
    lastFall = now;
    fallInterval = Math.max(140, 260 - score * 3);
    for (let i = eggs.length - 1; i >= 0; i--) {
      const e = eggs[i];
      e.step++;
      e.x += e.vx;
      e.y += e.vy;
      if (e.step >= STEPS) {
        if (e.target === wolfCol) {
          score++;
          sndCatch();
          // как в оригинале: каждые 200 очков промахи обнуляются
          if (score % 200 === 0 && score > 0) misses = 0;
        } else {
          misses++;
          flash = 14;
          sndMiss();
          if (misses >= 3) endGame();
        }
        eggs.splice(i, 1);
      }
    }
  }

  anim++;
  if (flash > 0) flash--;
}

function updateHud() {
  els.score.textContent = score;
  els.misses.textContent = `${misses}/3`;
  els.highscore.textContent = highScore;
}

function showOverlay(title, html, btn) {
  els.overlayTitle.textContent = title;
  els.overlayHint.innerHTML = html;
  els.start.textContent = btn;
  els.overlay.style.display = 'flex';
}
function hideOverlay() { els.overlay.style.display = 'none'; }

// ---------- рисование ----------
function drawSlopes(x0, y0, x1, y1) {
  ctx.strokeStyle = '#a68a4e';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  for (let s = 1; s < STEPS; s++) {
    const t = s / STEPS;
    const x = x0 + (x1 - x0) * t;
    const y = y0 + (y1 - y0) * t;
    const len = 14;
    ctx.beginPath();
    ctx.moveTo(x - len / 2, y);
    ctx.lineTo(x + len / 2, y);
    ctx.stroke();
  }
}

function drawEgg(e) {
  ctx.save();
  ctx.translate(e.x, e.y);
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = '#7a6a3f';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.ellipse(0, 0, EGG_R, EGG_R * 1.45, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#f5d762';
  ctx.beginPath();
  ctx.arc(1, 2, 2.6, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawWolf(x, y) {
  ctx.save();
  ctx.translate(x, y);
  if (facing < 0) ctx.scale(-1, 1);

  // хвост
  ctx.strokeStyle = '#5a5a5a';
  ctx.lineWidth = 6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(-24, 8);
  ctx.quadraticCurveTo(-40, -8, -30, -20);
  ctx.stroke();

  // лапы
  ctx.fillStyle = '#555';
  ctx.fillRect(-11, 18, 8, 14);
  ctx.fillRect(3, 18, 8, 14);

  // тело
  ctx.fillStyle = '#5a5a5a';
  ctx.beginPath();
  ctx.ellipse(2, 6, 17, 14, 0, 0, Math.PI * 2);
  ctx.fill();

  // голова
  ctx.beginPath();
  ctx.arc(18, -8, 13, 0, Math.PI * 2);
  ctx.fill();

  // уши
  ctx.beginPath();
  ctx.moveTo(12, -16);
  ctx.lineTo(6, -28);
  ctx.lineTo(16, -22);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(26, -16);
  ctx.lineTo(32, -28);
  ctx.lineTo(22, -22);
  ctx.fill();

  // морда
  ctx.fillStyle = '#d8d8d8';
  ctx.beginPath();
  ctx.ellipse(23, -2, 7, 6, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#222';
  ctx.beginPath();
  ctx.arc(28, -3, 2, 0, Math.PI * 2);
  ctx.fill();

  // глаз
  ctx.beginPath();
  ctx.arc(25, -11, 1.7, 0, Math.PI * 2);
  ctx.fill();

  // рот / корзинка
  ctx.strokeStyle = '#3a3a3a';
  ctx.lineWidth = 4;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(14, 1);
  ctx.lineTo(20, 7);
  ctx.stroke();

  ctx.restore();
}

function draw() {
  // фон
  ctx.fillStyle = '#efeadd';
  ctx.fillRect(0, 0, W, H);

  // пол
  ctx.fillStyle = '#8c7a4d';
  ctx.fillRect(0, H - 100, W, 100);
  ctx.fillStyle = '#6b5b39';
  ctx.fillRect(0, H - 100, W, 5);

  // горки и лотки
  drawSlopes(CHUTE_X[0], 35, 30, H - 105);
  drawSlopes(CHUTE_X[1], 35, W - 30, H - 105);
  ctx.fillStyle = '#8c7a4d';
  ctx.fillRect(CHUTE_X[0] - 12, 22, 24, 14);
  ctx.fillRect(CHUTE_X[1] - 12, 22, 24, 14);

  // яйца и волк
  eggs.forEach(drawEgg);
  drawWolf(COL_X[wolfCol], WOLF_Y);

  // вспышка при промахе
  if (flash > 0) {
    ctx.fillStyle = 'rgba(150, 40, 40, 0.22)';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#8a2b2b';
    ctx.font = 'bold 26px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('ПРОМАХ', W / 2, H / 2 - 18);
  }
}

// ---------- управление ----------
els.left.addEventListener('pointerdown', (e) => { e.preventDefault(); move(-1); });
els.right.addEventListener('pointerdown', (e) => { e.preventDefault(); move(1); });
els.start.addEventListener('click', (e) => { e.preventDefault(); resetGame(); });
els.sound.addEventListener('click', (e) => {
  e.preventDefault();
  soundEnabled = !soundEnabled;
  ensureAudio();
  els.sound.textContent = soundEnabled ? '🔊 Звук: включён' : '🔇 Звук: выключен';
});

window.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'a') { e.preventDefault(); move(-1); }
  else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'd') { e.preventDefault(); move(1); }
  else if ((e.key === 'Enter' || e.key === ' ') && state !== 'playing') {
    e.preventDefault();
    resetGame();
  }
});

canvas.addEventListener('pointerdown', (e) => {
  if (state !== 'playing') return;
  const r = canvas.getBoundingClientRect();
  const sx = (e.clientX - r.left) / r.width * W;
  move(sx < W / 2 ? -1 : 1);
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    const now = performance.now();
    lastSpawn = now;
    lastFall = now;
  }
});

function loop(now) {
  if (state === 'playing') update(now);
  draw();
  rafId = requestAnimationFrame(loop);
}

// старт
loadHighScore();
updateHud();
showOverlay('Волк ловит яйца', 'Лови яйца корзиной — не пропусти ни одного!<br>Пропустил 3 яйца — конец игры.', 'Старт');
draw();
