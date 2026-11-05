/* Пицца апокалипсиса — рендер, ввод, звук и интерфейс. */
(function () {
  "use strict";
  const E = window.PizzaEngine;
  const $ = (id) => document.getElementById(id);
  const canvas = $("road");
  const ctx = canvas.getContext("2d");
  const RECORD_KEY = "pizzaApocalypse.records.v1";
  const SOUND_KEY = "pizzaApocalypse.sound";
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reducedMotion = motionQuery.matches;
  motionQuery.addEventListener?.("change", (e) => { reducedMotion = e.matches; });

  let state = E.createState(20261105);
  let records = loadRecords();
  let soundOn = false;
  try { soundOn = localStorage.getItem(SOUND_KEY) === "1"; } catch {}
  let audio = null;
  let lastTime = 0;
  let accumulator = 0;
  let visualTime = 0;
  let radioUntil = 0;
  let bannerUntil = 0;
  let shake = 0;
  let flash = 0;
  let particles = [];
  let floaters = [];
  let W = 1000, H = 620, dpr = 1, narrow = false;
  let boostTapUntil = 0;
  const input = { fire: false, boost: false };
  const heldKeys = new Set();
  const pointerActions = new Map();

  function number(v) { return Math.floor(v).toLocaleString("ru-RU"); }
  function loadRecords() {
    try {
      const raw = JSON.parse(localStorage.getItem(RECORD_KEY) || "[]");
      if (!Array.isArray(raw)) return [];
      return raw.filter((r) => r && Number.isFinite(r.score) && r.score >= 0 && Number.isFinite(r.distance) && r.distance >= 0 && Number.isFinite(r.kills) && r.kills >= 0)
        .map((r) => ({ score: Math.floor(r.score), distance: Math.floor(r.distance), kills: Math.floor(r.kills), date: typeof r.date === "string" ? r.date.slice(0, 10) : "" }))
        .sort((a, b) => b.score - a.score).slice(0, 5);
    } catch { return []; }
  }
  function saveRecord() {
    const snap = E.snapshot(state);
    const previousBest = records[0]?.score || 0;
    const date = new Date().toISOString().slice(0, 10);
    records.push({ score: snap.score, distance: snap.distance, kills: snap.kills, date });
    records.sort((a, b) => b.score - a.score);
    records = records.slice(0, 5);
    try { localStorage.setItem(RECORD_KEY, JSON.stringify(records)); } catch { /* Private mode: the run still counts. */ }
    renderRecords();
    return snap.score > previousBest;
  }
  function renderRecords() {
    $("best").textContent = number(records[0]?.score || 0);
    const list = $("records");
    list.replaceChildren();
    if (!records.length) {
      const li = document.createElement("li");
      li.className = "empty-records";
      li.append("Здесь пока тихо.", document.createElement("br"));
      const span = document.createElement("span");
      span.textContent = "Испорти это первым заездом.";
      li.append(span); list.append(li); return;
    }
    for (const r of records) {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.textContent = number(r.distance) + " м · " + r.date;
      const strong = document.createElement("strong");
      strong.textContent = number(r.score);
      li.append(span, strong); list.append(li);
    }
  }

  function say(text, seconds = 3.5, force = false) {
    if (!force && visualTime < radioUntil) return;
    $("radio-message").textContent = text;
    radioUntil = visualTime + seconds;
  }
  function clearInput() {
    input.fire = false; input.boost = false; boostTapUntil = 0;
    heldKeys.clear(); pointerActions.clear();
    $("fire").classList.remove("pressed");
    $("boost").classList.remove("pressed");
  }
  function syncScreens(focusId) {
    $("start-screen").hidden = state.phase !== "ready";
    $("pause-screen").hidden = state.phase !== "paused";
    $("over-screen").hidden = state.phase !== "over";
    $("pause").disabled = state.phase === "ready" || state.phase === "over";
    $("pause").innerHTML = state.phase === "paused" ? "▶ <span>Продолжить</span>" : "❚❚ <span>Пауза</span>";
    $("pause").title = state.phase === "paused" ? "Продолжить (P)" : "Пауза (P)";
    for (const id of ["left", "right", "fire", "boost"]) $(id).disabled = state.phase !== "running";
    if (focusId) $(focusId).focus({ preventScroll: true });
    updateHud();
  }
  function start() {
    clearInput();
    state = E.startRun((Date.now() ^ Math.floor(Math.random() * 0xffffffff)) >>> 0);
    particles = []; floaters = []; shake = 0; flash = 0; accumulator = 0;
    bannerUntil = 0; $("district-banner").hidden = true;
    say("Заказ №666: «Четыре сыра, без мозгов». Клиент ждёт. Не корми клиента собой.", 6, true);
    syncScreens("road");
    if (soundOn) ensureAudio();
  }
  function pause() {
    if (E.pause(state)) { clearInput(); syncScreens("resume"); }
  }
  function resume() {
    if (E.resume(state)) { clearInput(); accumulator = 0; syncScreens("road"); if (soundOn) ensureAudio(); }
  }
  function togglePause() { if (state.phase === "running") pause(); else if (state.phase === "paused") resume(); }
  function garage() {
    clearInput(); state = E.createState(20261105); particles = []; floaters = [];
    $("district-banner").hidden = true;
    say("В гараже всё целое. Даже счёт за ремонт.", 4, true);
    syncScreens("start");
  }

  function updateHud() {
    const s = E.snapshot(state);
    for (const key of ["health", "fuel", "nitro"]) {
      $(key + "-value").textContent = s[key] + "%";
      const bar = $(key + "-bar");
      bar.firstElementChild.style.width = s[key] + "%";
      bar.setAttribute("aria-valuenow", String(s[key]));
      bar.parentElement.classList.toggle("critical", s[key] <= 20);
    }
    $("score").textContent = String(s.score).padStart(6, "0");
    $("distance").textContent = number(s.distance);
    $("speed").innerHTML = s.speed + " <small>КМ/Ч</small>";
    $("district").textContent = String(s.level).padStart(2, "0") + " / " + s.district;
    $("combo").hidden = s.combo <= 1 || state.phase !== "running";
    $("combo").textContent = "×" + s.combo + " СЕРИЯ";
    $("boost").classList.toggle("active", state.boostActive);
  }

  /* Web Audio: no downloads, no autoplay. */
  function ensureAudio() {
    if (!soundOn) return;
    try {
      if (!audio) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) throw new Error("No Web Audio");
        const ac = new AC();
        const master = ac.createGain(); master.gain.value = 0.22; master.connect(ac.destination);
        const engine = ac.createOscillator(); engine.type = "sawtooth";
        const filter = ac.createBiquadFilter(); filter.type = "lowpass"; filter.frequency.value = 180;
        const engineGain = ac.createGain(); engineGain.gain.value = 0;
        engine.connect(filter); filter.connect(engineGain); engineGain.connect(master); engine.start();
        audio = { ac, master, engine, engineGain };
      }
      if (audio.ac.state === "suspended") audio.ac.resume().catch(() => {});
    } catch {
      soundOn = false; updateSoundButton();
      say("Браузер не дал звук. Зато мотор рычит в воображении.", 4, true);
    }
  }
  function tone(freq, duration, type = "square", volume = 0.12, endFreq = freq) {
    if (!soundOn || !audio) return;
    const { ac, master } = audio;
    const osc = ac.createOscillator(), gain = ac.createGain();
    osc.type = type; osc.frequency.setValueAtTime(freq, ac.currentTime);
    osc.frequency.exponentialRampToValueAtTime(Math.max(20, endFreq), ac.currentTime + duration);
    gain.gain.setValueAtTime(volume, ac.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + duration);
    osc.connect(gain); gain.connect(master); osc.start(); osc.stop(ac.currentTime + duration);
    osc.onended = () => { osc.disconnect(); gain.disconnect(); };
  }
  function updateSoundButton() {
    $("sound").innerHTML = soundOn ? '<svg class="btn-icon"><use href="#i-sound"/></svg><span>Звук вкл.</span>' : '<svg class="btn-icon"><use href="#i-mute"/></svg><span>Звук выкл.</span>';
    $("sound").setAttribute("aria-pressed", String(soundOn));
    $("sound").title = soundOn ? "Выключить звук" : "Включить звук";
  }
  function toggleSound() {
    soundOn = !soundOn;
    if (soundOn) ensureAudio();
    if (audio) audio.master.gain.setTargetAtTime(soundOn ? 0.22 : 0, audio.ac.currentTime, 0.04);
    try { localStorage.setItem(SOUND_KEY, soundOn ? "1" : "0"); } catch {}
    updateSoundButton();
    if (soundOn) tone(440, 0.12, "sine", 0.15, 660);
  }

  function handleEvents(events) {
    for (const ev of events) {
      if (ev.text && ["crash", "pickup", "boost", "level", "lowfuel", "over"].includes(ev.type)) say(ev.text, 4, true);
      else if (ev.text && ev.type === "destroy") say(ev.text, 2.5);
      if (ev.type === "destroy" || ev.type === "ram") {
        burst(ev.x, ev.z, ev.type === "ram" ? "#65d6c3" : "#ff8a5c", 20);
        floaters.push({ x: ev.x, z: ev.z, text: "+" + ev.points, color: "#f5df4d", life: 1 });
        tone(ev.type === "ram" ? 130 : 220, 0.18, "sawtooth", 0.16, 45);
        if (ev.type === "ram") shake = Math.max(shake, 0.12);
      }
      if (ev.type === "hit") { burst(ev.x, ev.z, "#f5df4d", 5); tone(160, 0.05, "triangle", 0.07, 80); }
      if (ev.type === "crash") { burst(ev.x, ev.z, "#ff624d", 28); shake = 0.35; flash = 0.2; tone(90, 0.35, "sawtooth", 0.3, 25); }
      if (ev.type === "pickup") { burst(ev.x, ev.z, "#65d6c3", 12); tone(520, 0.16, "sine", 0.2, 1000); }
      if (ev.type === "near") floaters.push({ x: ev.x, z: ev.z, text: "+" + ev.points + " EDGE", color: "#65d6c3", life: 0.8 });
      if (ev.type === "boost") tone(100, 0.4, "sawtooth", 0.12, 380);
      if (ev.type === "level") {
        $("district-banner").textContent = ev.text;
        $("district-banner").hidden = false; bannerUntil = visualTime + 2.6;
        tone(330, 0.3, "triangle", 0.15, 660);
      }
      if (ev.type === "over") {
        clearInput();
        const isBest = saveRecord();
        const snap = E.snapshot(state);
        $("over-label").textContent = isBest ? "НОВЫЙ ЛИЧНЫЙ РЕКОРД!" : "ДОСТАВКА НЕ СОСТОЯЛАСЬ";
        $("over-title").textContent = ev.reason === "fuel" ? "СУХОЙ БАК." : "НУ ТЫ ДАЁШЬ.";
        $("over-reason").textContent = ev.text;
        $("result-score").textContent = number(snap.score);
        $("result-distance").textContent = number(snap.distance) + " м";
        $("result-kills").textContent = number(snap.kills);
        $("result-tip").textContent = ev.reason === "fuel"
          ? "Совет деда: канистры дают +28 бензина. Пицца-пушка их не взрывает — просто забери."
          : "Совет деда: нитро сносит всё на твоей полосе. Или расстреляй препятствие заранее.";
        syncScreens("restart");
        tone(220, 0.6, "sawtooth", 0.18, 40);
      }
    }
  }

  /* Projection: four lanes, a vanishing point, and deliberately chunky toy cars. */
  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    W = 1000; H = rect.height / rect.width * 1000; narrow = rect.width < 650;
  }
  new ResizeObserver(resize).observe(canvas);
  window.addEventListener("resize", resize);

  function roadWidth(z) { return 150 + Math.max(0, z) * 760; }
  function roadY(z) { return H * 0.23 + Math.pow(Math.max(0, z), 1.55) * H * 0.83; }
  function laneX(lane, z) { return W / 2 + (lane - 1.5) * roadWidth(z) / 4; }
  function scaleAt(z) { return (0.22 + Math.max(0, z) * 0.95) * (narrow ? 1.45 : 1); }
  function burst(lane, z, color, count) {
    if (reducedMotion) return;
    const x = laneX(lane, z), y = roadY(z);
    for (let i = 0; i < count; i++) {
      const a = Math.random() * Math.PI * 2, v = 50 + Math.random() * 220;
      particles.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 60, life: 0.4 + Math.random() * 0.5, max: 0.9, color, size: 3 + Math.random() * 7 });
    }
    if (particles.length > 240) particles.splice(0, particles.length - 240);
  }
  function roundRect(x, y, w, h, r, fill) {
    ctx.fillStyle = fill; ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
    ctx.fill();
  }
  function polygon(points, fill) {
    ctx.fillStyle = fill; ctx.beginPath(); ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1]);
    ctx.closePath(); ctx.fill();
  }
  function text(str, x, y, size, color, align = "center", font = "Oswald") {
    ctx.fillStyle = color; ctx.font = `700 ${size}px ${font}, sans-serif`; ctx.textAlign = align; ctx.fillText(str, x, y);
  }

  function drawBackground() {
    const d = E.district(state);
    const horizon = H * 0.23;
    const sky = ctx.createLinearGradient(0, 0, 0, horizon + 60);
    sky.addColorStop(0, "#111822"); sky.addColorStop(0.65, d.sky); sky.addColorStop(1, d.accent);
    ctx.fillStyle = sky; ctx.fillRect(0, 0, W, H);
    //A battered sun and distant skyline.
    ctx.fillStyle = "#ffc17b"; ctx.beginPath(); ctx.arc(735, horizon * 0.67, 49, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = d.sky;
    for (let i = 0; i < 5; i++) ctx.fillRect(680, horizon * 0.67 + i * 11, 110, 3 + i);
    const drift = reducedMotion ? 0 : Math.sin(visualTime * 0.03) * 8;
    for (let i = 0; i < 27; i++) {
      const x = i * 43 - 35 + drift;
      const h = 25 + ((i * 37) % 73);
      ctx.fillStyle = i % 2 ? "#20202c" : "#292331";
      ctx.fillRect(x, horizon - h + 12, 31 + (i % 3) * 7, h + 15);
      if (i % 4 === 0) polygon([[x, horizon - h + 12], [x + 14, horizon - h - 5], [x + 23, horizon - h + 12]], "#292331");
      ctx.fillStyle = "#e8a45a55";
      for (let wy = horizon - h + 23; wy < horizon; wy += 15) if ((i + wy) % 3) ctx.fillRect(x + 8, wy, 4, 6);
    }
    ctx.fillStyle = d.ground; ctx.fillRect(0, horizon + 12, W, H);
    //Ground stripes rush past the road.
    const offset = state.phase === "ready" ? (reducedMotion ? 0 : visualTime * 0.18 % 1) : state.roadOffset;
    for (let i = 0; i < 13; i++) {
      const z = ((i / 13 + offset) % 1) * 1.2;
      const y = roadY(z), h = 3 + z * 22;
      ctx.fillStyle = "#00000018"; ctx.fillRect(0, y, W, h);
    }
    polygon([[W / 2 - 86, horizon], [W / 2 + 86, horizon], [W / 2 + roadWidth(1.2) / 2 + 25, H], [W / 2 - roadWidth(1.2) / 2 - 25, H]], "#6b5550");
    polygon([[W / 2 - 75, horizon], [W / 2 + 75, horizon], [W / 2 + roadWidth(1.2) / 2, H], [W / 2 - roadWidth(1.2) / 2, H]], d.road);
    //Shoulder blocks.
    for (let i = 0; i < 18; i++) {
      const z1 = ((i / 18 + offset) % 1) * 1.2;
      const z2 = z1 + 0.028;
      for (const side of [-1, 1]) {
        const x1 = W / 2 + side * roadWidth(z1) / 2, x2 = W / 2 + side * roadWidth(z2) / 2;
        polygon([[x1, roadY(z1)], [x2, roadY(z2)], [x2 + side * (5 + z2 * 13), roadY(z2)], [x1 + side * (5 + z1 * 13), roadY(z1)]], i % 2 ? "#d5bca2" : "#a3483e");
      }
    }
    //Lane markings.
    for (let lane = 1; lane < 4; lane++) {
      for (let i = 0; i < 15; i++) {
        const z1 = ((i / 15 + offset) % 1) * 1.2;
        const z2 = z1 + 0.027;
        const x1 = W / 2 + (lane - 2) * roadWidth(z1) / 4;
        const x2 = W / 2 + (lane - 2) * roadWidth(z2) / 4;
        const w1 = 1 + z1 * 4, w2 = 1 + z2 * 4;
        polygon([[x1 - w1, roadY(z1)], [x1 + w1, roadY(z1)], [x2 + w2, roadY(z2)], [x2 - w2, roadY(z2)]], "#c8c0a17a");
      }
    }
    drawRoadside(offset);
    if (state.boostActive && !reducedMotion) {
      ctx.strokeStyle = "#65d6c34d"; ctx.lineWidth = 2;
      for (let i = 0; i < 16; i++) {
        const a = i / 16 * Math.PI * 2 + visualTime * 0.2;
        const r = 260 + ((visualTime * 500 + i * 47) % 350);
        ctx.beginPath(); ctx.moveTo(W / 2 + Math.cos(a) * r, H * 0.35 + Math.sin(a) * r * 0.7);
        ctx.lineTo(W / 2 + Math.cos(a) * (r + 90), H * 0.35 + Math.sin(a) * (r + 90) * 0.7); ctx.stroke();
      }
    }
  }

  function drawRoadside(offset) {
    for (let i = 0; i < 7; i++) {
      const z = ((i / 7 + offset * 0.55) % 1) * 1.15;
      if (z < 0.1) continue;
      const side = i % 2 ? 1 : -1;
      const x = W / 2 + side * (roadWidth(z) / 2 + 65 * scaleAt(z));
      const y = roadY(z), sc = scaleAt(z);
      ctx.save(); ctx.translate(x, y); ctx.scale(sc, sc);
      if (i % 3 === 0) {
        ctx.fillStyle = "#171b22"; ctx.fillRect(-4, -150, 8, 150);
        roundRect(-68, -164, 136, 65, 4, "#e8d9b8");
        ctx.strokeStyle = "#25252c"; ctx.lineWidth = 4; ctx.strokeRect(-63, -159, 126, 55);
        const signs = ["ШАУРМА\nБЕЗ ПОСЛЕДСТВИЙ", "ДО АДА\n2 КМ", "ОБГОНЯЙ\nПРОБЛЕМЫ", "СКИДКИ\nНА ГРОБЫ"];
        const lines = signs[Math.floor(i / 3) % signs.length].split("\n");
        text(lines[0], 0, -137, 16, "#25252c"); text(lines[1], 0, -116, 12, "#25252c");
      } else if (i % 3 === 1) {
        ctx.fillStyle = "#151b22"; ctx.fillRect(-3, -180, 6, 180);
        ctx.fillRect(-3, -180, side * 42, 5);
        ctx.fillStyle = "#f5df4d"; ctx.fillRect(side > 0 ? 28 : -42, -178, 14, 5);
        const glow = ctx.createRadialGradient(side * 34, -165, 0, side * 34, -165, 50);
        glow.addColorStop(0, "#f5df4d22"); glow.addColorStop(1, "#f5df4d00");
        ctx.fillStyle = glow; ctx.fillRect(-80, -220, 160, 130);
      } else {
        polygon([[-22, 0], [-15, -38], [8, -48], [28, -12], [20, 4]], "#44383c");
        ctx.strokeStyle = "#171b22"; ctx.lineWidth = 5;
        ctx.beginPath(); ctx.moveTo(0, -25); ctx.lineTo(-8, -75); ctx.lineTo(-30, -91); ctx.moveTo(-8, -75); ctx.lineTo(15, -96); ctx.stroke();
      }
      ctx.restore();
    }
  }

  function drawPizza(x, y, size, angle = 0) {
    ctx.save(); ctx.translate(x, y); ctx.rotate(angle);
    polygon([[-size * 0.6, -size * 0.5], [size * 0.6, -size * 0.5], [0, size * 0.75]], "#f7c65d");
    ctx.strokeStyle = "#c77b3e"; ctx.lineWidth = size * 0.18;
    ctx.beginPath(); ctx.moveTo(-size * 0.58, -size * 0.5); ctx.lineTo(size * 0.58, -size * 0.5); ctx.stroke();
    ctx.fillStyle = "#d94c3d";
    for (const [px, py] of [[-0.22, -0.22], [0.22, -0.2], [0, 0.25]]) { ctx.beginPath(); ctx.arc(px * size, py * size, size * 0.11, 0, Math.PI * 2); ctx.fill(); }
    ctx.restore();
  }
  function drawCar(x, y, sc, type, wobble = 0, hp = 1, maxHp = 1) {
    ctx.save(); ctx.translate(x, y); ctx.scale(sc, sc);
    ctx.fillStyle = "#00000055"; ctx.beginPath(); ctx.ellipse(0, 10, 48, 22, 0, 0, Math.PI * 2); ctx.fill();
    if (type === "player") {
      const tilt = (state.player.lane - state.player.x) * -0.12;
      ctx.rotate(tilt);
      if (state.boostActive) {
        const flame = reducedMotion ? 35 : 35 + Math.sin(visualTime * 45) * 14;
        polygon([[-24, 30], [-12, 30 + flame], [0, 35], [12, 30 + flame], [24, 30]], "#65d6c3");
        polygon([[-14, 30], [0, 48], [14, 30]], "#eaffff");
      }
      roundRect(-43, -38, 12, 28, 4, "#0b0e13"); roundRect(31, -38, 12, 28, 4, "#0b0e13");
      roundRect(-43, 13, 12, 28, 4, "#0b0e13"); roundRect(31, 13, 12, 28, 4, "#0b0e13");
      roundRect(-36, -58, 72, 105, 15, "#b84035");
      roundRect(-33, -61, 66, 101, 14, "#ff624d");
      roundRect(-27, -48, 54, 24, 6, "#202c38");
      polygon([[-24, -45], [20, -45], [26, -29], [-26, -29]], "#52717c");
      roundRect(-28, -19, 56, 40, 5, "#f5df4d");
      drawPizza(0, 1, 27, -0.1);
      roundRect(-26, 25, 52, 12, 3, "#26313b");
      roundRect(-30, 39, 60, 7, 2, "#d7d9ce");
      roundRect(-29, 34, 13, 6, 2, "#ffcf77"); roundRect(16, 34, 13, 6, 2, "#ffcf77");
      text("666", 0, 46, 8, "#1b2028");
      if (state.invulnerable > 0 && Math.floor(visualTime * 12) % 2) { ctx.globalAlpha = 0.35; roundRect(-36, -61, 72, 105, 14, "#ffffff"); ctx.globalAlpha = 1; }
    } else if (type === "taxi" || type === "van") {
      const van = type === "van";
      ctx.rotate(Math.sin(wobble) * 0.025);
      roundRect(-42, -42, 12, 29, 4, "#0b0e13"); roundRect(30, -42, 12, 29, 4, "#0b0e13");
      roundRect(-42, 17, 12, 29, 4, "#0b0e13"); roundRect(30, 17, 12, 29, 4, "#0b0e13");
      roundRect(-35, -62, 70, 112, van ? 7 : 14, van ? "#55566e" : "#b49c36");
      roundRect(-31, -59, 62, 103, van ? 5 : 12, van ? "#85869c" : "#e5c84d");
      roundRect(-25, -43, 50, 25, 5, "#263740");
      //Zombie driver.
      ctx.fillStyle = "#8fb879"; ctx.beginPath(); ctx.arc(8, -31, 10, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#17202a"; ctx.fillRect(3, -34, 4, 4); ctx.fillRect(11, -34, 4, 4);
      roundRect(-25, -10, 50, 34, 4, van ? "#626477" : "#d6b842");
      if (!van) { roundRect(-19, -5, 38, 13, 2, "#20252c"); text("TAXI", 0, 5, 10, "#f5df4d"); }
      else { ctx.strokeStyle = "#3d4052"; ctx.lineWidth = 4; ctx.strokeRect(-20, -5, 40, 28); text("БЮДЖЕТ", 0, 14, 9, "#e5e2d8"); }
      roundRect(-25, 29, 50, 12, 3, "#25303a");
      roundRect(-29, 43, 58, 6, 2, "#b7b9b4");
      roundRect(-29, 37, 12, 6, 2, "#ff624d"); roundRect(17, 37, 12, 6, 2, "#ff624d");
    } else if (type === "cone") {
      roundRect(-30, 10, 60, 16, 4, "#24252b");
      polygon([[-23, 12], [-7, -47], [7, -47], [23, 12]], "#ff8a4d");
      polygon([[-16, -12], [16, -12], [19, -1], [-19, -1]], "#f4e8d0");
      polygon([[-10, -34], [10, -34], [13, -25], [-13, -25]], "#f4e8d0");
    } else if (type === "barrier") {
      ctx.fillStyle = "#25262d"; ctx.fillRect(-40, -5, 9, 44); ctx.fillRect(31, -5, 9, 44);
      roundRect(-53, -35, 106, 40, 3, "#e9d8b5");
      ctx.save(); ctx.beginPath(); ctx.rect(-53, -35, 106, 40); ctx.clip();
      for (let i = -70; i < 80; i += 30) polygon([[i, -35], [i + 15, -35], [i + 45, 5], [i + 30, 5]], "#e45c43");
      ctx.restore();
      ctx.fillStyle = "#f5df4d"; ctx.beginPath(); ctx.arc(-42, -43, 6, 0, Math.PI * 2); ctx.arc(42, -43, 6, 0, Math.PI * 2); ctx.fill();
    }
    if (maxHp > 1 && hp < maxHp) {
      for (let i = 0; i < maxHp; i++) roundRect(-maxHp * 6 + i * 12, -78, 9, 5, 1, i < hp ? "#ff624d" : "#333640");
    }
    ctx.restore();
  }
  function drawPickup(e) {
    const x = laneX(e.x, e.z), y = roadY(e.z), sc = scaleAt(e.z);
    ctx.save(); ctx.translate(x, y - (reducedMotion ? 0 : Math.sin(e.wobble * 2) * 5 * sc)); ctx.scale(sc, sc);
    const colors = { fuel: "#f5df4d", repair: "#ff624d", nitro: "#65d6c3", pizza: "#f7c65d" };
    const color = colors[e.type];
    ctx.fillStyle = color + "22"; ctx.beginPath(); ctx.arc(0, -12, 43, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = color + "88"; ctx.lineWidth = 2; ctx.stroke();
    roundRect(-25, -37, 50, 50, 9, "#171e27");
    ctx.strokeStyle = color; ctx.lineWidth = 3;
    if (e.type === "fuel") {
      roundRect(-14, -27, 28, 32, 4, color); roundRect(-8, -34, 16, 7, 2, color);
      ctx.strokeStyle = "#171e27"; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(-8, -18); ctx.lineTo(8, -2); ctx.moveTo(8, -18); ctx.lineTo(-8, -2); ctx.stroke();
    } else if (e.type === "repair") {
      ctx.lineWidth = 8; ctx.beginPath(); ctx.moveTo(-11, 1); ctx.lineTo(9, -21); ctx.stroke();
      ctx.beginPath(); ctx.arc(12, -25, 9, 0.3, Math.PI * 1.7); ctx.stroke();
    } else if (e.type === "nitro") {
      polygon([[3, -34], [-15, -9], [-2, -9], [-7, 10], [16, -17], [3, -17]], color);
    } else drawPizza(0, -12, 34);
    ctx.restore();
  }

  function render(dt) {
    ctx.setTransform(canvas.width / W, 0, 0, canvas.height / H, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    if (shake > 0 && !reducedMotion) ctx.translate((Math.random() - 0.5) * shake * 35, (Math.random() - 0.5) * shake * 25);
    drawBackground();
    const drawables = state.entities.map((e) => ({ z: e.z, e }));
    if (state.phase === "ready") {
      //Attract-mode traffic, never confused with the actual run.
      const t = reducedMotion ? 0 : visualTime * 0.08;
      drawables.push({ z: 0.45 + (t % 0.25), e: { kind: "obstacle", type: "taxi", x: 2, z: 0.45 + (t % 0.25), hp: 2, maxHp: 2, wobble: visualTime } });
      drawables.push({ z: 0.68, e: { kind: "pickup", type: "fuel", x: 3, z: 0.68, wobble: visualTime } });
    }
    drawables.sort((a, b) => a.z - b.z);
    for (const { e } of drawables) {
      if (e.z < 0) continue;
      if (e.kind === "pickup") drawPickup(e);
      else drawCar(laneX(e.x, e.z), roadY(e.z), scaleAt(e.z), e.type, e.wobble, e.hp, e.maxHp);
    }
    for (const p of state.projectiles) drawPizza(laneX(p.x, p.z), roadY(p.z), 20 * scaleAt(p.z), p.spin);
    const px = laneX(state.player.x, E.PLAYER_Z), py = roadY(E.PLAYER_Z);
    drawCar(px, py, scaleAt(E.PLAYER_Z), "player");
    //Particles and score popups.
    for (const p of particles) { ctx.globalAlpha = Math.max(0, p.life / p.max); ctx.fillStyle = p.color; ctx.fillRect(p.x, p.y, p.size, p.size); }
    ctx.globalAlpha = 1;
    for (const f of floaters) { ctx.globalAlpha = Math.min(1, f.life * 2); text(f.text, laneX(f.x, f.z), roadY(f.z) - (1 - f.life) * 55, 25, f.color); }
    ctx.globalAlpha = 1;
    //Cinematic edge shade.
    const vignette = ctx.createRadialGradient(W / 2, H * 0.45, H * 0.2, W / 2, H * 0.45, Math.max(W, H) * 0.75);
    vignette.addColorStop(0, "#00000000"); vignette.addColorStop(1, "#00000066");
    ctx.fillStyle = vignette; ctx.fillRect(0, 0, W, H);
    if (flash > 0 && !reducedMotion) { ctx.fillStyle = `rgba(255,70,50,${flash * 0.65})`; ctx.fillRect(0, 0, W, H); }
    ctx.restore();
  }

  function tickEffects(dt) {
    shake = Math.max(0, shake - dt); flash = Math.max(0, flash - dt);
    for (const p of particles) { p.life -= dt; p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 220 * dt; }
    particles = particles.filter((p) => p.life > 0);
    for (const f of floaters) f.life -= dt;
    floaters = floaters.filter((f) => f.life > 0);
    if (bannerUntil && visualTime > bannerUntil) { $("district-banner").hidden = true; bannerUntil = 0; }
  }
  let hudTimer = 0, shotSoundTimer = 0;
  function frame(timestamp) {
    const dt = Math.min(0.1, Math.max(0, (timestamp - (lastTime || timestamp)) / 1000));
    lastTime = timestamp;
    visualTime += dt;
    if (state.phase === "running") {
      input.boost = heldKeys.has("ShiftLeft") || heldKeys.has("ShiftRight") || [...pointerActions.values()].includes("boost") || performance.now() < boostTapUntil;
      accumulator += dt;
      const events = [];
      while (accumulator >= 1 / 60) {
        events.push(...E.update(state, input, 1 / 60));
        accumulator -= 1 / 60;
        if (state.phase !== "running") { accumulator = 0; break; }
      }
      handleEvents(events);
      shotSoundTimer -= dt;
      if (input.fire && shotSoundTimer <= 0 && state.phase === "running") { tone(520, 0.07, "triangle", 0.07, 180); shotSoundTimer = 0.24; }
    }
    tickEffects(dt);
    hudTimer += dt;
    if (hudTimer >= 0.08) { updateHud(); hudTimer = 0; }
    if (audio) {
      const running = state.phase === "running" && soundOn;
      audio.engineGain.gain.setTargetAtTime(running ? 0.055 : 0, audio.ac.currentTime, 0.08);
      audio.engine.frequency.setTargetAtTime(35 + state.speed * 0.45, audio.ac.currentTime, 0.08);
    }
    render(dt);
    requestAnimationFrame(frame);
  }

  /* Keyboard uses physical keys, so Russian layouts work too. */
  const GAME_KEYS = new Set(["ArrowLeft", "ArrowRight", "KeyA", "KeyD", "Space", "ShiftLeft", "ShiftRight", "KeyP", "Escape", "Enter", "KeyH"]);
  window.addEventListener("keydown", (e) => {
    if ($("install-dialog")?.open) return;
    if (!GAME_KEYS.has(e.code) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target.isContentEditable) return;
    //Let focused buttons keep their native Enter/Space activation.
    if (e.target.closest?.("button, a") && (e.code === "Enter" || e.code === "Space")) return;
    e.preventDefault();
    if (e.code === "KeyP" || e.code === "Escape") { if (!e.repeat) togglePause(); return; }
    if (e.code === "Enter") { if (!e.repeat && (state.phase === "ready" || state.phase === "over")) start(); else if (!e.repeat && state.phase === "paused") resume(); return; }
    if (state.phase !== "running") return;
    heldKeys.add(e.code);
    if ((e.code === "ArrowLeft" || e.code === "KeyA") && !e.repeat) E.steer(state, -1);
    if ((e.code === "ArrowRight" || e.code === "KeyD") && !e.repeat) E.steer(state, 1);
    input.fire = heldKeys.has("Space"); input.boost = heldKeys.has("ShiftLeft") || heldKeys.has("ShiftRight");
    $("fire").classList.toggle("pressed", input.fire); $("boost").classList.toggle("pressed", input.boost);
  });
  window.addEventListener("keyup", (e) => {
    heldKeys.delete(e.code);
    input.fire = heldKeys.has("Space"); input.boost = heldKeys.has("ShiftLeft") || heldKeys.has("ShiftRight");
    $("fire").classList.toggle("pressed", input.fire); $("boost").classList.toggle("pressed", input.boost);
  });
  function bindHold(id, action) {
    const button = $(id);
    button.addEventListener("pointerdown", (e) => {
      if (state.phase !== "running" || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault(); button.setPointerCapture(e.pointerId);
      pointerActions.set(e.pointerId, action);
      if (action === "left") E.steer(state, -1);
      else if (action === "right") E.steer(state, 1);
      else { input[action] = true; if (action === "fire") E.fire(state); else boostTapUntil = performance.now() + 220; }
      button.classList.add("pressed");
    });
    const release = (e) => {
      const act = pointerActions.get(e.pointerId); pointerActions.delete(e.pointerId);
      if (act === "fire" || act === "boost") input[act] = [...pointerActions.values()].includes(act);
      button.classList.remove("pressed");
    };
    button.addEventListener("pointerup", release); button.addEventListener("pointercancel", release); button.addEventListener("lostpointercapture", release);
    //Keyboard/screen-reader activation of the on-screen buttons.
    button.addEventListener("click", (e) => {
      if (e.detail !== 0 || state.phase !== "running") return;
      if (action === "left") E.steer(state, -1);
      else if (action === "right") E.steer(state, 1);
      else if (action === "fire") E.fire(state);
      else boostTapUntil = performance.now() + 700;
    });
  }
  bindHold("left", "left"); bindHold("right", "right"); bindHold("fire", "fire"); bindHold("boost", "boost");
  $("start").addEventListener("click", start);
  $("restart").addEventListener("click", start);
  $("pause-restart").addEventListener("click", start);
  $("resume").addEventListener("click", resume);
  $("pause").addEventListener("click", togglePause);
  $("garage").addEventListener("click", garage);
  $("sound").addEventListener("click", toggleSound);
  window.addEventListener("blur", () => { clearInput(); pause(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { clearInput(); pause(); } });
  //Swipe steering directly on the road; buttons remain available for firing/boost.
  let swipe = null;
  canvas.addEventListener("pointerdown", (e) => { if (state.phase === "running") { swipe = { x: e.clientX, id: e.pointerId }; canvas.setPointerCapture(e.pointerId); } });
  canvas.addEventListener("pointermove", (e) => {
    if (!swipe || swipe.id !== e.pointerId || state.phase !== "running") return;
    const dx = e.clientX - swipe.x;
    if (Math.abs(dx) > 28) { E.steer(state, Math.sign(dx)); swipe.x = e.clientX; }
  });
  canvas.addEventListener("pointerup", () => { swipe = null; });
  canvas.addEventListener("pointercancel", () => { swipe = null; });

  renderRecords(); syncScreens(); resize(); updateSoundButton();
  //Audio context is created only after a user gesture, even if the saved preference is on.
  requestAnimationFrame(frame);

  //Small, read-only inspection surface for browser smoke tests.
  window.PizzaGame = Object.freeze({ snapshot: () => E.snapshot(state), version: "1.1.0" });
})();
