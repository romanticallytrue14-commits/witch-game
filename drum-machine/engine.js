/* Witchbeat's small, dependency-free pattern model. Safe to load in a browser or Node. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DrumMachineEngine = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STEP_COUNT = 16;
  const TRACKS = Object.freeze([
    Object.freeze({ id: "kick", name: "Бас-бочка", short: "КИК", hint: "Низкий удар", key: "K", color: "#ff718b" }),
    Object.freeze({ id: "snare", name: "Малый барабан", short: "СНЕЙР", hint: "Резкий акцент", key: "S", color: "#ffbd66" }),
    Object.freeze({ id: "hatClosed", name: "Закрытый хэт", short: "ЗАКР. ХЭТ", hint: "Короткий шум", key: "H", color: "#b6f36b" }),
    Object.freeze({ id: "hatOpen", name: "Открытый хэт", short: "ОТКР. ХЭТ", hint: "Длинный шум", key: "O", color: "#64d9d0" }),
    Object.freeze({ id: "clap", name: "Хлопок", short: "КЛЭП", hint: "Широкий хлопок", key: "C", color: "#bd9aff" })
  ]);

  const PATTERNS = Object.freeze({
    midnight: Object.freeze({
      name: "Полночь", style: "HOUSE", description: "Ровный пульс, мягкий хэт и широкие хлопки.", tempo: 120, swing: 6,
      steps: Object.freeze({
        kick: Object.freeze([0, 4, 8, 12]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 2, 4, 6, 8, 10, 12, 14]),
        hatOpen: Object.freeze([6, 14]),
        clap: Object.freeze([4, 12])
      })
    }),
    broken: Object.freeze({
      name: "Ломаный", style: "BREAK", description: "Смещённая бочка и живой, чуть неровный грув.", tempo: 108, swing: 14,
      steps: Object.freeze({
        kick: Object.freeze([0, 3, 7, 10, 13]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 2, 3, 4, 6, 8, 10, 11, 12, 14]),
        hatOpen: Object.freeze([7, 15]),
        clap: Object.freeze([12])
      })
    }),
    halfTime: Object.freeze({
      name: "Туман", style: "HALF-TIME", description: "Редкие удары, глубокая бочка и простор между ними.", tempo: 88, swing: 4,
      steps: Object.freeze({
        kick: Object.freeze([0, 6, 10]),
        snare: Object.freeze([8]),
        hatClosed: Object.freeze([0, 2, 4, 6, 8, 10, 12, 14]),
        hatOpen: Object.freeze([14]),
        clap: Object.freeze([8])
      })
    }),
    pulse: Object.freeze({
      name: "Пульс", style: "TECHNO", description: "Плотный прямой рисунок — хороший старт для эксперимента.", tempo: 132, swing: 0,
      steps: Object.freeze({
        kick: Object.freeze([0, 4, 8, 12]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]),
        hatOpen: Object.freeze([2, 6, 10, 14]),
        clap: Object.freeze([4, 12])
      })
    })
  });

  const TRACK_IDS = TRACKS.map((track) => track.id);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function blankSteps() {
    return Object.fromEntries(TRACK_IDS.map((id) => [id, Array(STEP_COUNT).fill(false)]));
  }

  function createPreset(id) {
    const key = Object.prototype.hasOwnProperty.call(PATTERNS, id) ? id : "midnight";
    const preset = PATTERNS[key];
    const steps = blankSteps();
    for (const track of TRACK_IDS) {
      for (const step of preset.steps[track] || []) {
        if (Number.isInteger(step) && step >= 0 && step < STEP_COUNT) steps[track][step] = true;
      }
    }
    return { preset: key, tempo: preset.tempo, swing: preset.swing, volume: 0.76, steps };
  }

  function sanitizeState(input) {
    if (!input || typeof input !== "object" || Array.isArray(input)) return createPreset("midnight");
    const requestedPreset = typeof input.preset === "string" && Object.prototype.hasOwnProperty.call(PATTERNS, input.preset)
      ? input.preset
      : "custom";
    const hasRows = TRACK_IDS.some((track) => input.steps && Array.isArray(input.steps[track]));
    const fallback = createPreset(requestedPreset === "custom" ? "midnight" : requestedPreset);
    const numberOr = (value, otherwise, min, max) =>
      typeof value === "number" && Number.isFinite(value) ? clamp(value, min, max) : otherwise;
    if (!hasRows && requestedPreset !== "custom") {
      fallback.tempo = numberOr(input.tempo, fallback.tempo, 60, 180);
      fallback.swing = numberOr(input.swing, fallback.swing, 0, 60);
      fallback.volume = numberOr(input.volume, fallback.volume, 0, 1);
      return fallback;
    }
    const preset = requestedPreset;
    const steps = blankSteps();
    for (const track of TRACK_IDS) {
      const row = input.steps && input.steps[track];
      if (!Array.isArray(row)) continue;
      for (let step = 0; step < STEP_COUNT; step++) steps[track][step] = row[step] === true;
    }
    return {
      preset,
      tempo: numberOr(input.tempo, fallback.tempo, 60, 180),
      swing: numberOr(input.swing, fallback.swing, 0, 60),
      volume: numberOr(input.volume, fallback.volume, 0, 1),
      steps
    };
  }

  function toggleStep(state, trackId, step) {
    if (!TRACK_IDS.includes(trackId) || !Number.isInteger(step) || step < 0 || step >= STEP_COUNT) return state;
    const next = sanitizeState(state);
    next.steps[trackId][step] = !next.steps[trackId][step];
    next.preset = "custom";
    return next;
  }

  function randomizeState(state, random) {
    const next = sanitizeState(state);
    const rng = typeof random === "function" ? random : Math.random;
    const steps = blankSteps();
    const backbeat = new Set([4, 12]);
    const kickOdds = (step) => step % 4 === 0 ? 0.76 : step % 2 === 0 ? 0.13 : 0.035;
    const snareOdds = (step) => backbeat.has(step) ? 0.78 : step % 4 === 2 ? 0.09 : 0.025;
    const closedOdds = (step) => step % 2 === 0 ? 0.82 : 0.36;
    const openOdds = (step) => step % 8 === 6 ? 0.32 : 0.035;
    const clapOdds = (step) => backbeat.has(step) ? 0.3 : 0.025;
    const odds = { kick: kickOdds, snare: snareOdds, hatClosed: closedOdds, hatOpen: openOdds, clap: clapOdds };
    for (const track of TRACK_IDS) {
      for (let step = 0; step < STEP_COUNT; step++) steps[track][step] = rng() < odds[track](step);
    }
    next.preset = "custom";
    next.steps = steps;
    return next;
  }

  return Object.freeze({ STEP_COUNT, TRACKS, PATTERNS, clamp, blankSteps, createPreset, sanitizeState, toggleStep, randomizeState });
});
