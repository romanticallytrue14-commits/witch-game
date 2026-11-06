/* Witchbeat's dependency-free pattern model. Safe to load in a browser or Node. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DrumMachineEngine = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STEP_COUNT = 16;
  const TB303_NOTES = Object.freeze([
    Object.freeze({ midi: 36, name: "C2", short: "C" }),
    Object.freeze({ midi: 38, name: "D2", short: "D" }),
    Object.freeze({ midi: 39, name: "Eb2", short: "Eb" }),
    Object.freeze({ midi: 41, name: "F2", short: "F" }),
    Object.freeze({ midi: 43, name: "G2", short: "G" }),
    Object.freeze({ midi: 46, name: "Bb2", short: "Bb" }),
    Object.freeze({ midi: 48, name: "C3", short: "C↑" })
  ]);

  const TRACKS = Object.freeze([
    Object.freeze({ id: "kick", name: "Бас-бочка", short: "КИК", hint: "Низкий удар", key: "K", color: "#ff718b" }),
    Object.freeze({ id: "snare", name: "Малый барабан", short: "СНЕЙР", hint: "Резкий акцент", key: "S", color: "#ffbd66" }),
    Object.freeze({ id: "hatClosed", name: "Закрытый хэт", short: "ЗАКР. ХЭТ", hint: "Короткий шум", key: "H", color: "#b6f36b" }),
    Object.freeze({ id: "hatOpen", name: "Открытый хэт", short: "ОТКР. ХЭТ", hint: "Длинный шум", key: "O", color: "#64d9d0" }),
    Object.freeze({ id: "clap", name: "Хлопок", short: "КЛЭП", hint: "Широкий хлопок", key: "C", color: "#bd9aff" }),
    Object.freeze({ id: "tb303", name: "TB-303", short: "TB-303", hint: "Кислотный бас", key: "303", color: "#ff9d54", synth: true })
  ]);

  const PATTERNS = Object.freeze({
    midnight: Object.freeze({
      name: "Полночь", style: "HOUSE", description: "Ровный пульс, мягкий хэт и кислотная бас-линия.", tempo: 120, swing: 6,
      steps: Object.freeze({
        kick: Object.freeze([0, 4, 8, 12]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 2, 4, 6, 8, 10, 12, 14]),
        hatOpen: Object.freeze([6, 14]),
        clap: Object.freeze([4, 12]),
        tb303: Object.freeze([0, 3, 6, 8, 11, 14])
      }),
      tbNotes: Object.freeze([0, 2, 4, 5, 4, 2, 1, 3, 0, 4, 5, 2, 0, 3, 1, 4])
    }),
    broken: Object.freeze({
      name: "Ломаный", style: "BREAK", description: "Смещённая бочка и пружинящий TB-303 с лёгким свингом.", tempo: 108, swing: 14,
      steps: Object.freeze({
        kick: Object.freeze([0, 3, 7, 10, 13]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 2, 3, 4, 6, 8, 10, 11, 12, 14]),
        hatOpen: Object.freeze([7, 15]),
        clap: Object.freeze([12]),
        tb303: Object.freeze([0, 2, 5, 7, 9, 12, 15])
      }),
      tbNotes: Object.freeze([0, 4, 5, 2, 3, 4, 1, 5, 0, 2, 4, 5, 3, 1, 6, 5])
    }),
    halfTime: Object.freeze({
      name: "Туман", style: "HALF-TIME", description: "Редкие удары, глубокая бочка и просторный басовый рифф.", tempo: 88, swing: 4,
      steps: Object.freeze({
        kick: Object.freeze([0, 6, 10]),
        snare: Object.freeze([8]),
        hatClosed: Object.freeze([0, 2, 4, 6, 8, 10, 12, 14]),
        hatOpen: Object.freeze([14]),
        clap: Object.freeze([8]),
        tb303: Object.freeze([0, 5, 8, 11, 14])
      }),
      tbNotes: Object.freeze([0, 0, 2, 0, 3, 5, 0, 2, 4, 0, 1, 5, 0, 3, 4, 0])
    }),
    pulse: Object.freeze({
      name: "Пульс", style: "TECHNO", description: "Плотный прямой рисунок и цепкий кислотный бас.", tempo: 132, swing: 0,
      steps: Object.freeze({
        kick: Object.freeze([0, 4, 8, 12]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]),
        hatOpen: Object.freeze([2, 6, 10, 14]),
        clap: Object.freeze([4, 12]),
        tb303: Object.freeze([0, 2, 4, 7, 8, 10, 12, 14])
      }),
      tbNotes: Object.freeze([0, 2, 4, 3, 0, 5, 2, 4, 0, 3, 5, 2, 0, 4, 6, 3])
    })
  });

  const TRACK_IDS = TRACKS.map((track) => track.id);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function blankSteps() {
    return Object.fromEntries(TRACK_IDS.map((id) => [id, Array(STEP_COUNT).fill(false)]));
  }

  function blankNotes() {
    return Array(STEP_COUNT).fill(0);
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
    return {
      preset: key,
      tempo: preset.tempo,
      swing: preset.swing,
      volume: 0.76,
      tb303Cutoff: 2500,
      tb303Resonance: 68,
      tb303Notes: Array.from({ length: STEP_COUNT }, (_, i) => preset.tbNotes[i] ?? 0),
      steps
    };
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
      fallback.tb303Cutoff = numberOr(input.tb303Cutoff, fallback.tb303Cutoff, 250, 8500);
      fallback.tb303Resonance = numberOr(input.tb303Resonance, fallback.tb303Resonance, 0, 100);
      return fallback;
    }

    const steps = blankSteps();
    for (const track of TRACK_IDS) {
      const row = input.steps && input.steps[track];
      if (Array.isArray(row)) {
        for (let step = 0; step < STEP_COUNT; step++) steps[track][step] = row[step] === true;
      } else if (track === "tb303" && requestedPreset !== "custom") {
        // Migrate saved built-in patterns from the five-drum-track version.
        steps.tb303 = fallback.steps.tb303.slice();
      }
    }

    const savedNotes = Array.isArray(input.tb303Notes) ? input.tb303Notes : fallback.tb303Notes;
    const tb303Notes = Array.from({ length: STEP_COUNT }, (_, step) => {
      const candidate = savedNotes[step];
      return Number.isInteger(candidate) ? clamp(candidate, 0, TB303_NOTES.length - 1) : fallback.tb303Notes[step];
    });

    return {
      preset: requestedPreset,
      tempo: numberOr(input.tempo, fallback.tempo, 60, 180),
      swing: numberOr(input.swing, fallback.swing, 0, 60),
      volume: numberOr(input.volume, fallback.volume, 0, 1),
      tb303Cutoff: numberOr(input.tb303Cutoff, fallback.tb303Cutoff, 250, 8500),
      tb303Resonance: numberOr(input.tb303Resonance, fallback.tb303Resonance, 0, 100),
      tb303Notes,
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

  function cycleTb303Note(state, step) {
    if (!Number.isInteger(step) || step < 0 || step >= STEP_COUNT) return state;
    const next = sanitizeState(state);
    next.steps.tb303[step] = true;
    next.tb303Notes[step] = (next.tb303Notes[step] + 1) % TB303_NOTES.length;
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
    const tbOdds = (step) => step % 4 === 0 ? 0.5 : step % 2 === 0 ? 0.22 : 0.08;
    const odds = { kick: kickOdds, snare: snareOdds, hatClosed: closedOdds, hatOpen: openOdds, clap: clapOdds, tb303: tbOdds };
    for (const track of TRACK_IDS) {
      for (let step = 0; step < STEP_COUNT; step++) steps[track][step] = rng() < odds[track](step);
    }
    next.preset = "custom";
    next.steps = steps;
    next.tb303Notes = Array.from({ length: STEP_COUNT }, () => Math.min(TB303_NOTES.length - 1, Math.floor(rng() * TB303_NOTES.length)));
    return next;
  }

  return Object.freeze({
    STEP_COUNT,
    TB303_NOTES,
    TRACKS,
    PATTERNS,
    clamp,
    blankSteps,
    createPreset,
    sanitizeState,
    toggleStep,
    cycleTb303Note,
    randomizeState
  });
});
