"use strict";

const $ = (id) => document.getElementById(id);

/* ============================================================
   Данные игры
   ============================================================ */

const STORAGE_KEY = "witchGame3";
const LEGACY_KEY = "witchGame2";
const SOUND_KEY = "witchGameSound";

const ITEMS = {
  гриб: { name: "Гриб", emoji: "🍄", color: [138, 106, 69] },
  трава: { name: "Трава", emoji: "🌿", color: [111, 154, 88] },
  ягода: { name: "Ягода", emoji: "🫐", color: [122, 107, 176] },
  цветок: { name: "Лунный цветок", emoji: "🌙", color: [200, 212, 255] }
};

const ITEM_KEYS = Object.keys(ITEMS);
const COMMON_ITEMS = ["гриб", "трава", "ягода"];

// Чем пахнет ингредиент — из этого собирается подсказка гостя.
const SMELL = {
  гриб: "сыростью и старым пнём",
  трава: "горькой зеленью",
  ягода: "кислой лесной сладостью",
  цветок: "холодным лунным светом"
};

const RECIPES = [
  { id: "night-eye", name: "Ночное зрение", guest: "Слепой охотник", items: ["гриб", "ягода"], reward: 12 },
  { id: "healing", name: "Лесное исцеление", guest: "Раненая странница", items: ["трава", "гриб"], reward: 10 },
  { id: "shadow", name: "Шёпот теней", guest: "Незнакомец в плаще", items: ["ягода", "трава"], reward: 15 },
  { id: "dreamless", name: "Сон без сновидений", guest: "Уставшая ведьма", items: ["гриб", "гриб"], reward: 11 },
  { id: "moonpath", name: "Лунная дорожка", guest: "Заблудший путник", items: ["цветок", "трава"], reward: 22 },
  { id: "fog", name: "Туман забвения", guest: "Старуха с веретеном", items: ["цветок", "ягода"], reward: 25 },
  { id: "call", name: "Зов леса", guest: "Лесной дух", items: ["цветок", "гриб"], reward: 20 }
];

const UPGRADES = [
  {
    id: "bed",
    name: "🌿 Травяная грядка",
    desc: "Сбор на опушке приносит два ингредиента вместо одного.",
    cost: 20
  },
  {
    id: "orb",
    name: "🔮 Хрустальный шар",
    desc: "Подсказки гримуара для гостей бесплатны.",
    cost: 25
  }
];

const HINT_COST = 4;
const MAX_TRIES = 2;
const STREAK_BONUS_CAP = 5;

/* ============================================================
   Состояние и сохранение
   ============================================================ */

function freshState() {
  return {
    coins: 0,
    served: 0,
    leftGuests: 0,
    streak: 0,
    stock: { гриб: 2, трава: 2, ягода: 2, цветок: 0 },
    pot: [],
    discovered: [],
    upgrades: [],
    order: null
  };
}

function toCount(value, fallback = 0) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.floor(n)) : fallback;
}

function makeOrder(prevId) {
  const pool = RECIPES.filter((r) => r.id !== prevId);
  const chosen = pool[Math.floor(Math.random() * pool.length)] || RECIPES[0];
  return { recipeId: chosen.id, tries: MAX_TRIES, revealed: [] };
}

function sanitize(raw, base) {
  if (!raw || typeof raw !== "object") return base;

  base.coins = toCount(raw.coins);
  base.served = toCount(raw.served);
  base.leftGuests = toCount(raw.leftGuests);
  base.streak = toCount(raw.streak);

  const stock = raw.stock && typeof raw.stock === "object" ? raw.stock : {};
  for (const key of ITEM_KEYS) {
    base.stock[key] = toCount(stock[key], base.stock[key]);
  }

  if (Array.isArray(raw.pot)) {
    base.pot = raw.pot.filter((item) => ITEM_KEYS.includes(item)).slice(0, 2);
  }

  if (Array.isArray(raw.discovered)) {
    base.discovered = raw.discovered.filter((id) => RECIPES.some((r) => r.id === id));
  }

  if (Array.isArray(raw.upgrades)) {
    base.upgrades = raw.upgrades.filter((id) => UPGRADES.some((u) => u.id === id));
  }

  const order = raw.order;
  if (order && RECIPES.some((r) => r.id === order.recipeId)) {
    base.order = {
      recipeId: order.recipeId,
      tries: Math.min(MAX_TRIES, Math.max(1, toCount(order.tries, MAX_TRIES))),
      revealed: Array.isArray(order.revealed)
        ? order.revealed.filter((item) => ITEM_KEYS.includes(item))
        : []
    };
  }

  return base;
}

function loadState() {
  const base = freshState();

  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved) return sanitize(saved, base);
  } catch (e) { /* битое сохранение — начинаем аккуратно */ }

  // Миграция со старой версии игры.
  try {
    const legacy = JSON.parse(localStorage.getItem(LEGACY_KEY) || "null");
    if (legacy && typeof legacy === "object") {
      base.coins = toCount(legacy.coins);
      base.served = toCount(legacy.served);
      for (const key of ITEM_KEYS) {
        if (legacy.stock) base.stock[key] = toCount(legacy.stock[key], base.stock[key]);
      }
    }
  } catch (e) { /* старых данных нет — и хорошо */ }

  return base;
}

let state = loadState();
if (!state.order) state.order = makeOrder(null);

let storageWarned = false;

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (e) {
    // Приватный режим или переполненное хранилище — играем без сохранения.
    if (!storageWarned) {
      storageWarned = true;
      forestMessage("Браузер не разрешает сохранение — прогресс не сохранится.");
    }
  }
}

/* ============================================================
   Вспомогательные функции
   ============================================================ */

function recipeById(id) {
  return RECIPES.find((r) => r.id === id);
}

function currentRecipe() {
  const recipe = state.order && recipeById(state.order.recipeId);
  if (recipe) return recipe;
  state.order = makeOrder(null);
  return recipeById(state.order.recipeId);
}

function hasUpgrade(id) {
  return state.upgrades.includes(id);
}

function hintCost() {
  return hasUpgrade("orb") ? 0 : HINT_COST;
}

function uniqueItems(items) {
  return [...new Set(items)];
}

function itemLabel(key) {
  return ITEMS[key].emoji + " " + ITEMS[key].name;
}

function smellLine(items) {
  const parts = uniqueItems(items).map((key) => SMELL[key]);
  return parts.join(", а ещё ");
}

function rewardBonus() {
  return Math.min(state.streak, STREAK_BONUS_CAP);
}

function message(text) {
  const node = $("status");
  node.textContent = text;
  node.classList.remove("pulse");
  // перезапуск анимации
  void node.offsetWidth;
  node.classList.add("pulse");
}

function forestMessage(text) {
  $("forest-message").textContent = text;
}

/* ============================================================
   Отрисовка интерфейса
   ============================================================ */

function buildIngredients() {
  const wrap = $("ingredients");
  wrap.innerHTML = "";

  for (const key of ITEM_KEYS) {
    const item = ITEMS[key];
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ingredient";
    button.dataset.item = key;

    const emoji = document.createElement("span");
    emoji.className = "ing-emoji";
    emoji.textContent = item.emoji;

    const name = document.createElement("span");
    name.className = "ing-name";
    name.textContent = item.name;

    const count = document.createElement("span");
    count.className = "ing-count";
    count.textContent = "0";

    button.append(emoji, name, count);
    button.addEventListener("click", () => addToPot(key));
    wrap.appendChild(button);
  }
}

function updateIngredientUI() {
  for (const key of ITEM_KEYS) {
    const button = document.querySelector('[data-item="' + key + '"]');
    if (!button) continue;
    const amount = state.stock[key] || 0;
    button.querySelector(".ing-count").textContent = amount;
    button.classList.toggle("empty", amount === 0);
    button.setAttribute("aria-label", ITEMS[key].name + ": в запасе " + amount);
  }

  const chips = ITEM_KEYS.map((key) => {
    const amount = state.stock[key] || 0;
    return (
      '<span class="chip' + (amount ? "" : " chip-empty") + '">' +
      ITEMS[key].emoji + " " + amount +
      "</span>"
    );
  });
  $("stock").innerHTML = chips.join("");
}

function renderGrimoire() {
  const list = $("grimoire");
  list.innerHTML = "";

  for (const recipe of RECIPES) {
    const known = state.discovered.includes(recipe.id);
    const li = document.createElement("li");
    li.className = "grim " + (known ? "known" : "unknown");

    const name = document.createElement("span");
    name.className = "grim-name";
    name.textContent = known ? recipe.name : "???";

    const meta = document.createElement("span");
    meta.className = "grim-meta";
    meta.textContent = known
      ? recipe.guest + " • " +
        uniqueItems(recipe.items).map(itemLabel).join(" + ") +
        " • " + recipe.reward + " 🪙"
      : recipe.guest + " • рецепт ещё не открыт";

    li.append(name, meta);
    list.appendChild(li);
  }
}

function renderUpgrades() {
  const wrap = $("upgrades");
  wrap.innerHTML = "";

  for (const upgrade of UPGRADES) {
    const owned = hasUpgrade(upgrade.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "upgrade" + (owned ? " owned" : "");
    button.disabled = owned;

    const name = document.createElement("span");
    name.className = "up-name";
    name.textContent = upgrade.name;

    const desc = document.createElement("span");
    desc.className = "up-desc";
    desc.textContent = upgrade.desc;

    const cost = document.createElement("span");
    cost.className = "up-cost";
    cost.textContent = owned ? "куплено" : upgrade.cost + " 🪙";

    button.append(name, desc, cost);
    if (!owned) button.addEventListener("click", () => buyUpgrade(upgrade.id));
    wrap.appendChild(button);
  }
}

function updateUI() {
  const recipe = currentRecipe();

  $("stat-coins").textContent = state.coins;
  $("stat-served").textContent = state.served;
  $("stat-streak").textContent = state.streak;
  $("stat-left").textContent = state.leftGuests;

  $("order").textContent = recipe.guest + " просит: «" + recipe.name + "»";
  $("guest-hint").textContent =
    "Гость бормочет: «Пахнет " + smellLine(recipe.items) + "»";

  const bonus = rewardBonus();
  $("reward").textContent =
    "Награда: " + recipe.reward + " 🪙" +
    (bonus ? " + " + bonus + " 🪙 за серию" : "");

  const attempts = $("attempts");
  attempts.textContent = "попыток: " + state.order.tries;
  attempts.classList.toggle("spent", state.order.tries < MAX_TRIES);

  const known = uniqueItems(recipe.items);
  const revealed = state.order.revealed;
  $("revealed").textContent = revealed.length
    ? "Гримуар: " + known.map((key) =>
        revealed.includes(key) ? itemLabel(key) : "❔ ???"
      ).join(" • ")
    : "";

  const hint = $("hint");
  const cost = hintCost();
  const allKnown = known.every((key) => revealed.includes(key));
  hint.textContent = allKnown
    ? "🕯 Гримуар уже всё рассказал"
    : cost
      ? "🕯 Подсказка — " + cost + " 🪙"
      : "🕯 Подсказка — бесплатно";
  hint.disabled = allKnown;

  updateIngredientUI();
  renderGrimoire();
  renderUpgrades();
}

/* ============================================================
   Действия игрока
   ============================================================ */

function addToPot(key) {
  if (state.pot.length >= 2) {
    message("В котле уже два ингредиента. Свари зелье или очисти котёл.");
    return;
  }

  if ((state.stock[key] || 0) <= 0) {
    message("«" + ITEMS[key].name + "» закончился. Отправляйся в лес!");
    return;
  }

  state.stock[key]--;
  state.pot.push(key);
  save();
  updateUI();
  playPluck();
  message("В котле: " + state.pot.map(itemLabel).join(" + "));
}

function clearPot() {
  if (!state.pot.length) {
    message("Котёл и так пуст.");
    return;
  }

  for (const item of state.pot) state.stock[item] = (state.stock[item] || 0) + 1;
  state.pot = [];
  save();
  updateUI();
  message("Ингредиенты возвращены в запас.");
}

function brew() {
  if (state.pot.length !== 2) {
    message("Для зелья нужны ровно два ингредиента.");
    return;
  }

  const recipe = currentRecipe();
  const wanted = [...recipe.items].sort().join("|");
  const mixed = [...state.pot].sort().join("|");
  state.pot = [];

  if (wanted === mixed) {
    const bonus = rewardBonus();
    state.coins += recipe.reward + bonus;
    state.served++;
    state.streak++;
    if (!state.discovered.includes(recipe.id)) state.discovered.push(recipe.id);

    state.order = makeOrder(recipe.id);
    save();
    updateUI();
    playSuccess();
    flash("good");
    message(
      "Зелье готово! «" + recipe.name + "» — гость платит " +
      recipe.reward + " 🪙" +
      (bonus ? " и ещё " + bonus + " 🪙 за серию" : "") + "."
    );
    return;
  }

  state.order.tries--;
  state.streak = 0;
  playFail();
  flash("bad");

  if (state.order.tries > 0) {
    save();
    updateUI();
    message(
      "Из котла вырвался чёрный дым — это не тот рецепт. " +
      "Осталось попыток: " + state.order.tries + "."
    );
    return;
  }

  state.leftGuests++;
  state.order = makeOrder(recipe.id);
  save();
  updateUI();
  message("Гость ушёл, не дождавшись. Заказ потерян, серия сброшена.");
}

function gatherEdge() {
  const found = COMMON_ITEMS[Math.floor(Math.random() * COMMON_ITEMS.length)];
  state.stock[found] = (state.stock[found] || 0) + 1;

  let text = "На опушке ты нашла " + itemLabel(found) + ".";

  if (hasUpgrade("bed")) {
    const second = COMMON_ITEMS[Math.floor(Math.random() * COMMON_ITEMS.length)];
    state.stock[second] = (state.stock[second] || 0) + 1;
    text += " Грядка дала ещё " + itemLabel(second) + ".";
  }

  save();
  updateUI();
  playPluck();
  forestMessage(text);
}

function gatherDeep() {
  const roll = Math.random();

  if (roll < 0.16) {
    playFail();
    forestMessage(
      "Тень скользнула между стволов, и ты вернулась с пустыми руками."
    );
    return;
  }

  if (roll < 0.52) {
    state.stock["цветок"] = (state.stock["цветок"] || 0) + 1;
    save();
    updateUI();
    playSuccess();
    forestMessage(
      "В чаще, где не бывает солнца, раскрылся 🌙 Лунный цветок. Редкая удача!"
    );
    return;
  }

  const found = COMMON_ITEMS[Math.floor(Math.random() * COMMON_ITEMS.length)];
  state.stock[found] = (state.stock[found] || 0) + 1;
  save();
  updateUI();
  playPluck();
  forestMessage("В чаще ты отыскала " + itemLabel(found) + ".");
}

function useHint() {
  const recipe = currentRecipe();
  const known = uniqueItems(recipe.items);
  const missing = known.filter((key) => !state.order.revealed.includes(key));
  const cost = hintCost();

  if (!missing.length) {
    message("Гримуар уже рассказал об этом зелье всё.");
    return;
  }

  if (state.coins < cost) {
    message(
      "Не хватает монет: подсказка стоит " + cost + " 🪙, " +
      "а у тебя " + state.coins + "."
    );
    return;
  }

  state.coins -= cost;
  state.order.revealed.push(missing[0]);
  save();
  updateUI();
  playHint();
  message(
    "🕯 Гримуар шепчет: в «" + recipe.name + "» входит " +
    itemLabel(missing[0]) + "."
  );
}

function buyUpgrade(id) {
  const upgrade = UPGRADES.find((u) => u.id === id);
  if (!upgrade || hasUpgrade(id)) return;

  if (state.coins < upgrade.cost) {
    message(
      "Нужно " + upgrade.cost + " 🪙, а у тебя " + state.coins + ". " +
      "Сначала обслужи гостей."
    );
    return;
  }

  state.coins -= upgrade.cost;
  state.upgrades.push(id);
  save();
  updateUI();
  playUpgrade();
  message(upgrade.name + " теперь в лавке.");
}

function resetGame() {
  if (!window.confirm("Начать заново? Монеты, рецепты и запас исчезнут.")) return;

  state = freshState();
  state.order = makeOrder(null);
  save();
  updateUI();
  forestMessage("Между деревьями что-то мерцает...");
  message("Новая история начинается. Котёл чист, гость уже стучит.");
  flash("bad");
}

/* ============================================================
   Звук
   ============================================================ */

let audioCtx = null;
let master = null;
let droneStarted = false;
let soundOn = false;

try {
  soundOn = localStorage.getItem(SOUND_KEY) === "1";
} catch (e) { /* нет доступа к настройке */ }

function ensureAudio() {
  if (!audioCtx) {
    const AudioEngine = window.AudioContext || window.webkitAudioContext;
    if (!AudioEngine) return null;

    audioCtx = new AudioEngine();
    master = audioCtx.createGain();
    master.gain.value = soundOn ? 0.9 : 0;
    master.connect(audioCtx.destination);
  }

  if (!droneStarted) startDrone();
  if (soundOn && audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function startDrone() {
  droneStarted = true;

  const gain = audioCtx.createGain();
  gain.gain.value = 0.045;

  const filter = audioCtx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = 240;
  filter.Q.value = 1.2;

  filter.connect(gain);
  gain.connect(master);

  [55, 82.5, 110].forEach((freq, index) => {
    const osc = audioCtx.createOscillator();
    osc.type = index === 2 ? "triangle" : "sine";
    osc.frequency.value = freq;
    osc.detune.value = index === 1 ? 7 : 0;

    const voice = audioCtx.createGain();
    voice.gain.value = index === 2 ? 0.18 : 0.55;
    osc.connect(voice);
    voice.connect(filter);
    osc.start();
  });

  // Медленное дыхание фильтра — «ветер» вокруг избушки.
  const lfo = audioCtx.createOscillator();
  lfo.frequency.value = 0.07;
  const lfoGain = audioCtx.createGain();
  lfoGain.gain.value = 90;
  lfo.connect(lfoGain);
  lfoGain.connect(filter.frequency);
  lfo.start();
}

function tone(freq, options = {}) {
  if (!audioCtx || !soundOn) return;

  const type = options.type || "sine";
  const duration = options.duration || 0.25;
  const level = options.level || 0.1;
  const delay = options.delay || 0;
  const slideTo = options.slideTo || null;

  const start = audioCtx.currentTime + delay;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();

  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, start + duration);

  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(level, start + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);

  osc.connect(gain);
  gain.connect(master);
  osc.start(start);
  osc.stop(start + duration + 0.05);
}

function playPluck() {
  tone(620, { type: "triangle", duration: 0.14, level: 0.07, slideTo: 880 });
}

function playHint() {
  tone(880, { duration: 0.2, level: 0.05 });
  tone(1174, { duration: 0.24, level: 0.04, delay: 0.1 });
}

function playSuccess() {
  [523.25, 659.25, 783.99].forEach((freq, index) => {
    tone(freq, { duration: 0.32, level: 0.075, delay: index * 0.08 });
  });
  tone(1046.5, { duration: 0.5, level: 0.05, delay: 0.26 });
}

function playFail() {
  tone(190, { type: "sawtooth", duration: 0.5, level: 0.07, slideTo: 70 });
  tone(96, { duration: 0.6, level: 0.05, delay: 0.05 });
}

function playUpgrade() {
  tone(392, { type: "triangle", duration: 0.22, level: 0.06 });
  tone(587.33, { type: "triangle", duration: 0.3, level: 0.05, delay: 0.1 });
}

function setSoundLabel() {
  const button = $("sound");
  button.textContent = soundOn ? "🔊 Выключить звук" : "🔇 Включить звук";
  button.setAttribute("aria-pressed", String(soundOn));
}

function setSound(on) {
  soundOn = on;

  try {
    localStorage.setItem(SOUND_KEY, on ? "1" : "0");
  } catch (e) { /* настройка не сохранится — не беда */ }

  ensureAudio();

  if (master) {
    master.gain.setTargetAtTime(soundOn ? 0.9 : 0, audioCtx.currentTime, 0.15);
  }

  setSoundLabel();
}

/* ============================================================
   Сцена на canvas
   ============================================================ */

const SCENE_W = 600;
const SCENE_H = 340;
const canvas = $("scene");
const ctx = canvas.getContext("2d");

const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

let flashState = null; // { type: "good" | "bad", at: ms }

function flash(type) {
  // При отключённой анимации кадр не обновляется — вспышка застыла бы на экране.
  if (motionQuery.matches) return;
  flashState = { type, at: performance.now() };
}

function mulberry32(seed) {
  let a = seed;
  return function random() {
    a |= 0;
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(20261105);

const STARS = Array.from({ length: 70 }, () => ({
  x: rand() * SCENE_W,
  y: rand() * 205,
  r: 0.4 + rand() * 1.2,
  phase: rand() * Math.PI * 2
}));

const FIREFLIES = Array.from({ length: 9 }, (_, index) => ({
  x: 300 + index * 31,
  y: 90 + ((index * 41) % 140),
  phase: index * 0.9
}));

function sizeCanvas() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = SCENE_W * dpr;
  canvas.height = SCENE_H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function potColor() {
  if (!state.pot.length) return [89, 123, 88];

  let r = 0;
  let g = 0;
  let b = 0;
  for (const key of state.pot) {
    const color = ITEMS[key].color;
    r += color[0];
    g += color[1];
    b += color[2];
  }

  const n = state.pot.length;
  return [
    Math.min(255, (r / n) * 1.25),
    Math.min(255, (g / n) * 1.25),
    Math.min(255, (b / n) * 1.2)
  ].map((value) => Math.round(value));
}

function drawGuest(t, x, baseY) {
  const breathe = Math.sin(t * 1.6) * 1.2;

  ctx.save();
  ctx.shadowColor = "rgba(0, 0, 0, 0.65)";
  ctx.shadowBlur = 14;
  ctx.fillStyle = "#0c1210";

  ctx.beginPath();
  ctx.moveTo(x - 15, baseY);
  ctx.quadraticCurveTo(x - 12, baseY - 44 + breathe, x - 4, baseY - 52 + breathe);
  ctx.quadraticCurveTo(x + 4, baseY - 52 + breathe, x + 12, baseY - 44 + breathe);
  ctx.quadraticCurveTo(x + 15, baseY - 20, x + 16, baseY);
  ctx.closePath();
  ctx.fill();

  ctx.beginPath();
  ctx.arc(x, baseY - 58 + breathe, 9.5, 0, Math.PI * 2);
  ctx.fill();

  ctx.shadowColor = "#9ecf8a";
  ctx.shadowBlur = 9;
  ctx.fillStyle = "rgba(180, 220, 150, 0.85)";
  ctx.beginPath();
  ctx.arc(x + 2, baseY - 58 + breathe, 1.6, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();
}

function drawScene(now) {
  const t = now / 1000;

  // Небо
  const sky = ctx.createLinearGradient(0, 0, 0, SCENE_H);
  sky.addColorStop(0, "#101b1a");
  sky.addColorStop(1, "#34362a");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, SCENE_W, SCENE_H);

  // Звёзды
  for (const star of STARS) {
    const twinkle = 0.35 + 0.35 * Math.sin(t * 1.3 + star.phase);
    ctx.fillStyle = "rgba(214, 226, 196, " + twinkle.toFixed(3) + ")";
    ctx.beginPath();
    ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
    ctx.fill();
  }

  // Луна
  const moonGlow = ctx.createRadialGradient(490, 62, 12, 490, 62, 90);
  moonGlow.addColorStop(0, "rgba(206, 214, 176, 0.28)");
  moonGlow.addColorStop(1, "rgba(206, 214, 176, 0)");
  ctx.fillStyle = moonGlow;
  ctx.fillRect(400, 0, 200, 160);

  ctx.fillStyle = "#c5c9a8";
  ctx.shadowColor = "#ced6b0";
  ctx.shadowBlur = 25;
  ctx.beginPath();
  ctx.arc(490, 62, 25, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;

  ctx.fillStyle = "rgba(150, 156, 130, 0.55)";
  [[483, 54, 5], [499, 70, 4], [493, 46, 3], [505, 58, 2.5]].forEach(([x, y, r]) => {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  });

  // Дальний лес
  ctx.fillStyle = "#0e1714";
  for (let i = 0; i < 16; i++) {
    const x = i * 40 - 12;
    const height = 70 + ((i * 29) % 45);
    ctx.beginPath();
    ctx.moveTo(x, 246);
    ctx.lineTo(x + 22, 246 - height);
    ctx.lineTo(x + 44, 246);
    ctx.closePath();
    ctx.fill();
  }

  // Ближние деревья
  for (let i = 0; i < 12; i++) {
    const x = i * 56 - 10;
    const height = 100 + ((i * 37) % 75);
    ctx.fillStyle = i % 2 ? "#101d18" : "#15231c";
    ctx.beginPath();
    ctx.moveTo(x, 245);
    ctx.lineTo(x + 28, 245 - height);
    ctx.lineTo(x + 56, 245);
    ctx.closePath();
    ctx.fill();
  }

  // Земля
  const ground = ctx.createLinearGradient(0, 240, 0, SCENE_H);
  ground.addColorStop(0, "#161b13");
  ground.addColorStop(1, "#0d100c");
  ctx.fillStyle = ground;
  ctx.fillRect(0, 240, SCENE_W, SCENE_H - 240);

  // Избушка
  ctx.fillStyle = "#30271e";
  ctx.fillRect(90, 139, 185, 135);

  ctx.fillStyle = "#1b1915";
  ctx.beginPath();
  ctx.moveTo(70, 145);
  ctx.lineTo(181, 67);
  ctx.lineTo(295, 145);
  ctx.closePath();
  ctx.fill();

  // Труба и дым
  ctx.fillStyle = "#231d18";
  ctx.fillRect(232, 96, 17, 42);

  for (let i = 0; i < 6; i++) {
    const progress = ((t * 16 + i * 22) % 130) / 130;
    const y = 96 - progress * 90;
    const x = 240 + Math.sin(t * 1.4 + i) * (6 + progress * 16);
    ctx.fillStyle = "rgba(165, 190, 161, " + (0.15 * (1 - progress)).toFixed(3) + ")";
    ctx.beginPath();
    ctx.arc(x, y, 8 + progress * 14, 0, Math.PI * 2);
    ctx.fill();
  }

  // Окно
  const flicker = 0.82 + Math.sin(t * 3) * 0.08 + Math.sin(t * 7.3) * 0.04;
  ctx.shadowColor = "#e2a64f";
  ctx.shadowBlur = 18 + Math.sin(t * 3) * 5;
  ctx.fillStyle = "rgba(225, 170, 86, " + flicker.toFixed(3) + ")";
  ctx.fillRect(116, 163, 48, 48);
  ctx.shadowBlur = 0;

  ctx.strokeStyle = "#3a281d";
  ctx.lineWidth = 5;
  ctx.strokeRect(116, 163, 48, 48);
  ctx.beginPath();
  ctx.moveTo(140, 163);
  ctx.lineTo(140, 211);
  ctx.moveTo(116, 187);
  ctx.lineTo(164, 187);
  ctx.stroke();

  // Связки трав на стене
  ctx.strokeStyle = "#5c6f43";
  ctx.lineWidth = 2;
  for (let i = 0; i < 3; i++) {
    const x = 98 + i * 9;
    ctx.beginPath();
    ctx.moveTo(x, 148);
    ctx.lineTo(x + 2, 170);
    ctx.stroke();
  }

  // Дверь
  ctx.fillStyle = "#171410";
  ctx.fillRect(204, 175, 47, 99);
  ctx.fillStyle = "#ba9c63";
  ctx.beginPath();
  ctx.arc(240, 226, 3, 0, Math.PI * 2);
  ctx.fill();

  // Гость
  drawGuest(t, 320, 274);

  // Свечение котла
  const [pr, pg, pb] = potColor();
  const glowStrength = state.pot.length ? 0.42 : 0.26;
  const glow = ctx.createRadialGradient(405, 252, 8, 405, 252, 130);
  glow.addColorStop(
    0,
    "rgba(" + pr + ", " + pg + ", " + pb + ", " +
    (glowStrength + Math.sin(t * 2) * 0.06).toFixed(3) + ")"
  );
  glow.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = glow;
  ctx.fillRect(280, 130, 300, 190);

  // Огонь под котлом
  const flameX = 405;
  ctx.fillStyle = "#c0642c";
  ctx.shadowColor = "#e8903e";
  ctx.shadowBlur = 18;
  ctx.beginPath();
  ctx.moveTo(374, 302);
  ctx.lineTo(387, 270 - Math.sin(t * 8) * 5);
  ctx.lineTo(399, 298);
  ctx.lineTo(414, 265 - Math.sin(t * 7) * 5);
  ctx.lineTo(433, 302);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = "rgba(240, 170, 90, 0.85)";
  ctx.beginPath();
  ctx.moveTo(392, 300);
  ctx.lineTo(403, 279 - Math.sin(t * 9) * 4);
  ctx.lineTo(416, 300);
  ctx.closePath();
  ctx.fill();
  ctx.shadowBlur = 0;

  // Тело котла
  ctx.fillStyle = "#111412";
  ctx.beginPath();
  ctx.ellipse(flameX, 259, 54, 38, 0, 0, Math.PI);
  ctx.fill();
  ctx.fillRect(351, 246, 108, 14);

  // Варево
  ctx.fillStyle = "rgb(" + pr + ", " + pg + ", " + pb + ")";
  ctx.beginPath();
  ctx.ellipse(flameX, 247, 50, 9, 0, 0, Math.PI * 2);
  ctx.fill();

  // Пузырьки
  const bubbleCount = state.pot.length ? 6 : 4;
  for (let i = 0; i < bubbleCount; i++) {
    const rise = Math.abs(Math.sin(t * 2 + i * 1.3));
    ctx.fillStyle = "rgba(214, 235, 190, 0.75)";
    ctx.beginPath();
    ctx.arc(
      372 + i * 13,
      248 - rise * (state.pot.length ? 12 : 8),
      1.4 + (i % 2) * 0.8,
      0,
      Math.PI * 2
    );
    ctx.fill();
  }

  // Светлячки
  for (const fly of FIREFLIES) {
    const x = fly.x + Math.sin(t * 0.9 + fly.phase) * 10;
    const y = fly.y + Math.cos(t * 0.7 + fly.phase) * 9;
    const alpha = 0.35 + 0.4 * Math.abs(Math.sin(t * 1.4 + fly.phase));
    ctx.fillStyle = "rgba(194, 215, 145, " + alpha.toFixed(3) + ")";
    ctx.beginPath();
    ctx.arc(x, y, 2, 0, Math.PI * 2);
    ctx.fill();
  }

  // Вспышки событий
  if (flashState) {
    const age = now - flashState.at;
    if (age < 900) {
      const progress = age / 900;
      if (flashState.type === "good") {
        for (let i = 0; i < 16; i++) {
          const angle = (i / 16) * Math.PI * 2;
          const distance = 20 + progress * 90;
          const alpha = Math.max(0, 0.9 - progress);
          ctx.fillStyle = "rgba(240, 214, 140, " + alpha.toFixed(3) + ")";
          ctx.beginPath();
          ctx.arc(
            flameX + Math.cos(angle) * distance,
            250 + Math.sin(angle) * distance * 0.6,
            2.2 * (1 - progress) + 0.4,
            0,
            Math.PI * 2
          );
          ctx.fill();
        }
      } else {
        ctx.fillStyle = "rgba(40, 24, 20, " + (0.5 * (1 - progress)).toFixed(3) + ")";
        ctx.fillRect(0, 0, SCENE_W, SCENE_H);
      }
    } else {
      flashState = null;
    }
  }
}

let rafId = null;

function sceneLoop(now) {
  drawScene(now);
  rafId = requestAnimationFrame(sceneLoop);
}

function startScene() {
  sizeCanvas();
  cancelAnimationFrame(rafId);

  if (motionQuery.matches) {
    drawScene(performance.now());
    return;
  }

  rafId = requestAnimationFrame(sceneLoop);
}

/* ============================================================
   Запуск
   ============================================================ */

function init() {
  buildIngredients();

  $("hint").addEventListener("click", useHint);
  $("brew").addEventListener("click", brew);
  $("clear").addEventListener("click", clearPot);
  $("gather-edge").addEventListener("click", gatherEdge);
  $("gather-deep").addEventListener("click", gatherDeep);
  $("reset").addEventListener("click", resetGame);
  $("sound").addEventListener("click", () => setSound(!soundOn));

  // Звук можно включить только после жеста пользователя.
  document.addEventListener(
    "pointerdown",
    () => {
      if (soundOn) ensureAudio();
    },
    { once: true }
  );

  window.addEventListener("resize", () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== SCENE_W * dpr) {
      sizeCanvas();
      if (motionQuery.matches) drawScene(performance.now());
    }
  });

  const onMotionChange = () => startScene();
  if (motionQuery.addEventListener) motionQuery.addEventListener("change", onMotionChange);
  else if (motionQuery.addListener) motionQuery.addListener(onMotionChange);

  setSoundLabel();
  updateUI();
  save();
  startScene();
}

init();
