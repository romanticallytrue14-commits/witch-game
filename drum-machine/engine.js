/* Witchbeat's dependency-free pattern model. Safe to load in a browser or Node. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DrumMachineEngine = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STEP_COUNT = 16;
  const SCALE_DEGREES = Object.freeze([
    Object.freeze({ semitones: 0, pitch: "C", short: "C" }),
    Object.freeze({ semitones: 2, pitch: "D", short: "D" }),
    Object.freeze({ semitones: 3, pitch: "Eb", short: "Eb" }),
    Object.freeze({ semitones: 5, pitch: "F", short: "F" }),
    Object.freeze({ semitones: 7, pitch: "G", short: "G" }),
    Object.freeze({ semitones: 10, pitch: "Bb", short: "Bb" }),
    Object.freeze({ semitones: 12, pitch: "C", short: "C↑" })
  ]);
  const makeCMinorScale = (rootMidi) => Object.freeze(SCALE_DEGREES.map((degree) => {
    const midi = rootMidi + degree.semitones;
    return Object.freeze({ midi, name: degree.pitch + (Math.floor(midi / 12) - 1), short: degree.short });
  }));

  const TB303_NOTES = makeCMinorScale(36);
  const NOTE_SCALES = Object.freeze({
    tb303: TB303_NOTES,
    piano: makeCMinorScale(60),
    organ: makeCMinorScale(48),
    strings: makeCMinorScale(60),
    bell: makeCMinorScale(72)
  });
  const NOTE_FIELDS = Object.freeze({
    tb303: "tb303Notes",
    piano: "pianoNotes",
    organ: "organNotes",
    strings: "stringsNotes",
    bell: "bellNotes"
  });
  const NOTE_TRACK_IDS = Object.freeze(Object.keys(NOTE_FIELDS));
  const DEFAULT_NOTES = Object.freeze({
    tb303: Object.freeze([0, 2, 4, 5, 4, 2, 1, 3, 0, 4, 5, 2, 0, 3, 1, 4]),
    piano: Object.freeze([0, 2, 4, 2, 3, 4, 2, 0, 0, 2, 4, 5, 4, 2, 1, 0]),
    organ: Object.freeze([0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0]),
    strings: Object.freeze([0, 0, 0, 0, 3, 0, 0, 0, 5, 0, 0, 0, 3, 0, 0, 0]),
    bell: Object.freeze([4, 0, 0, 2, 0, 3, 0, 4, 0, 0, 3, 0, 2, 0, 4, 0])
  });
  const LEGACY_STRINGS_STEPS = Object.freeze({
    midnight: Object.freeze([0, 8]),
    broken: Object.freeze([1, 9]),
    halfTime: Object.freeze([0, 8]),
    pulse: Object.freeze([0, 8])
  });

  const TRACKS = Object.freeze([
    Object.freeze({ id: "kick", name: "Бас-бочка", short: "КИК", hint: "Низкий удар", key: "K", color: "#ff718b" }),
    Object.freeze({ id: "snare", name: "Малый барабан", short: "СНЕЙР", hint: "Резкий акцент", key: "S", color: "#ffbd66" }),
    Object.freeze({ id: "hatClosed", name: "Закрытый хэт", short: "ЗАКР. ХЭТ", hint: "Короткий шум", key: "H", color: "#b6f36b" }),
    Object.freeze({ id: "hatOpen", name: "Открытый хэт", short: "ОТКР. ХЭТ", hint: "Длинный шум", key: "O", color: "#64d9d0" }),
    Object.freeze({ id: "clap", name: "Хлопок", short: "КЛЭП", hint: "Широкий хлопок", key: "C", color: "#bd9aff" }),
    Object.freeze({ id: "tb303", name: "TB-303", short: "TB-303", hint: "Кислотный бас", key: "303", color: "#ff9d54", synth: true }),
    Object.freeze({ id: "piano", name: "Пианино", short: "ПИАНО", hint: "Перкуссионные клавиши", key: "P", color: "#85b7ff", synth: true }),
    Object.freeze({ id: "organ", name: "Орган", short: "ОРГАН", hint: "Тёплый аккордовый звук", key: "O", color: "#e59bff", synth: true }),
    Object.freeze({ id: "strings", name: "Струнные", short: "СТРУНЫ", hint: "Мелодия по нотам", key: "S", color: "#7be0bd", synth: true }),
    Object.freeze({ id: "bell", name: "Колокольчик", short: "БЕЛЛ", hint: "Звонкие ноты", key: "B", color: "#ffd36e", synth: true })
  ]);

  const PATTERNS = Object.freeze({
    midnight: Object.freeze({
      name: "Полночь", style: "HOUSE", description: "Ровный пульс, мягкий хэт, струнная мелодия и кислотная бас-линия.", tempo: 120, swing: 6,
      steps: Object.freeze({
        kick: Object.freeze([0, 4, 8, 12]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 2, 4, 6, 8, 10, 12, 14]),
        hatOpen: Object.freeze([6, 14]),
        clap: Object.freeze([4, 12]),
        tb303: Object.freeze([0, 3, 6, 8, 11, 14]),
        piano: Object.freeze([0, 8]),
        organ: Object.freeze([4, 12]),
        strings: Object.freeze([0, 4, 8, 12]),
        bell: Object.freeze([6, 14])
      }),
      tbNotes: Object.freeze([0, 2, 4, 5, 4, 2, 1, 3, 0, 4, 5, 2, 0, 3, 1, 4]),
      stringsNotes: Object.freeze([0, 0, 0, 0, 3, 0, 0, 0, 4, 0, 0, 0, 2, 0, 0, 0])
    }),
    broken: Object.freeze({
      name: "Ломаный", style: "BREAK", description: "Смещённая бочка, лёгкий свинг и нисходящий струнный ход.", tempo: 108, swing: 14,
      steps: Object.freeze({
        kick: Object.freeze([0, 3, 7, 10, 13]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 2, 3, 4, 6, 8, 10, 11, 12, 14]),
        hatOpen: Object.freeze([7, 15]),
        clap: Object.freeze([12]),
        tb303: Object.freeze([0, 2, 5, 7, 9, 12, 15]),
        piano: Object.freeze([0, 7, 12]),
        organ: Object.freeze([4, 12]),
        strings: Object.freeze([1, 5, 9, 13]),
        bell: Object.freeze([3, 11, 15])
      }),
      tbNotes: Object.freeze([0, 4, 5, 2, 3, 4, 1, 5, 0, 2, 4, 5, 3, 1, 6, 5]),
      stringsNotes: Object.freeze([0, 4, 0, 0, 0, 2, 0, 0, 0, 5, 0, 0, 0, 3, 0, 0])
    }),
    halfTime: Object.freeze({
      name: "Туман", style: "HALF-TIME", description: "Редкие удары, глубокая бочка и просторная струнная мелодия.", tempo: 88, swing: 4,
      steps: Object.freeze({
        kick: Object.freeze([0, 6, 10]),
        snare: Object.freeze([8]),
        hatClosed: Object.freeze([0, 2, 4, 6, 8, 10, 12, 14]),
        hatOpen: Object.freeze([14]),
        clap: Object.freeze([8]),
        tb303: Object.freeze([0, 5, 8, 11, 14]),
        piano: Object.freeze([0, 8]),
        organ: Object.freeze([8]),
        strings: Object.freeze([0, 8, 12]),
        bell: Object.freeze([6, 14])
      }),
      tbNotes: Object.freeze([0, 0, 2, 0, 3, 5, 0, 2, 4, 0, 1, 5, 0, 3, 4, 0]),
      stringsNotes: Object.freeze([0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 2, 0, 0, 0])
    }),
    pulse: Object.freeze({
      name: "Пульс", style: "TECHNO", description: "Плотный прямой рисунок, кислотный бас и упругая струнная тема.", tempo: 132, swing: 0,
      steps: Object.freeze({
        kick: Object.freeze([0, 4, 8, 12]),
        snare: Object.freeze([4, 12]),
        hatClosed: Object.freeze([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]),
        hatOpen: Object.freeze([2, 6, 10, 14]),
        clap: Object.freeze([4, 12]),
        tb303: Object.freeze([0, 2, 4, 7, 8, 10, 12, 14]),
        piano: Object.freeze([0, 4, 8, 12]),
        organ: Object.freeze([4, 12]),
        strings: Object.freeze([0, 4, 8, 12]),
        bell: Object.freeze([2, 6, 10, 14])
      }),
      tbNotes: Object.freeze([0, 2, 4, 3, 0, 5, 2, 4, 0, 3, 5, 2, 0, 4, 6, 3]),
      stringsNotes: Object.freeze([0, 0, 0, 0, 5, 0, 0, 0, 4, 0, 0, 0, 6, 0, 0, 0])
    })
  });

  const TRACK_IDS = TRACKS.map((track) => track.id);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function rowMatchesActiveSteps(row, activeSteps) {
    return Array.isArray(row) && row.length === STEP_COUNT &&
      row.every((active, step) => active === activeSteps.includes(step));
  }

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
    const state = {
      preset: key,
      tempo: preset.tempo,
      swing: preset.swing,
      volume: 0.76,
      tb303Cutoff: 2500,
      tb303Resonance: 68,
      steps
    };
    for (const trackId of NOTE_TRACK_IDS) {
      const field = NOTE_FIELDS[trackId];
      const source = Array.isArray(preset[field])
        ? preset[field]
        : trackId === "tb303" ? preset.tbNotes : DEFAULT_NOTES[trackId];
      state[field] = Array.from({ length: STEP_COUNT }, (_, step) => source[step] ?? 0);
    }
    return state;
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
      if (track === "strings" && requestedPreset !== "custom" &&
          rowMatchesActiveSteps(row, LEGACY_STRINGS_STEPS[requestedPreset])) {
        // Upgrade untouched built-in string rows to the new preset melody.
        steps[track] = fallback.steps[track].slice();
      } else if (Array.isArray(row)) {
        for (let step = 0; step < STEP_COUNT; step++) steps[track][step] = row[step] === true;
      } else if (requestedPreset !== "custom") {
        // Add newly introduced lanes to older saved built-in grooves.
        steps[track] = fallback.steps[track].slice();
      }
    }

    const notes = {};
    for (const trackId of NOTE_TRACK_IDS) {
      const field = NOTE_FIELDS[trackId];
      const scale = NOTE_SCALES[trackId];
      const saved = Array.isArray(input[field]) ? input[field] : null;
      const legacyStringsNotes = trackId === "strings" && requestedPreset !== "custom" &&
        saved && saved.length === STEP_COUNT &&
        saved.every((note, step) => note === DEFAULT_NOTES.strings[step]);
      const source = saved && !legacyStringsNotes ? saved : fallback[field];
      notes[field] = Array.from({ length: STEP_COUNT }, (_, step) => {
        const candidate = source[step];
        return Number.isInteger(candidate) ? clamp(candidate, 0, scale.length - 1) : fallback[field][step];
      });
    }

    return {
      preset: requestedPreset,
      tempo: numberOr(input.tempo, fallback.tempo, 60, 180),
      swing: numberOr(input.swing, fallback.swing, 0, 60),
      volume: numberOr(input.volume, fallback.volume, 0, 1),
      tb303Cutoff: numberOr(input.tb303Cutoff, fallback.tb303Cutoff, 250, 8500),
      tb303Resonance: numberOr(input.tb303Resonance, fallback.tb303Resonance, 0, 100),
      ...notes,
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

  function cycleInstrumentNote(state, trackId, step) {
    if (!NOTE_FIELDS[trackId] || !Number.isInteger(step) || step < 0 || step >= STEP_COUNT) return state;
    const next = sanitizeState(state);
    next.steps[trackId][step] = true;
    const field = NOTE_FIELDS[trackId];
    next[field][step] = (next[field][step] + 1) % NOTE_SCALES[trackId].length;
    next.preset = "custom";
    return next;
  }

  function cycleTb303Note(state, step) {
    return cycleInstrumentNote(state, "tb303", step);
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
    const pianoOdds = (step) => step % 8 === 0 ? 0.42 : step % 4 === 2 ? 0.1 : 0.025;
    const organOdds = (step) => step % 8 === 4 ? 0.28 : 0.035;
    const stringsOdds = (step) => step % 8 === 0 ? 0.38 : 0.02;
    const bellOdds = (step) => step % 4 === 2 ? 0.22 : 0.035;
    const odds = {
      kick: kickOdds, snare: snareOdds, hatClosed: closedOdds, hatOpen: openOdds, clap: clapOdds,
      tb303: tbOdds, piano: pianoOdds, organ: organOdds, strings: stringsOdds, bell: bellOdds
    };
    for (const track of TRACK_IDS) {
      for (let step = 0; step < STEP_COUNT; step++) steps[track][step] = rng() < odds[track](step);
    }
    next.preset = "custom";
    next.steps = steps;
    for (const trackId of NOTE_TRACK_IDS) {
      const field = NOTE_FIELDS[trackId];
      next[field] = Array.from({ length: STEP_COUNT }, () => Math.min(
        NOTE_SCALES[trackId].length - 1,
        Math.floor(rng() * NOTE_SCALES[trackId].length)
      ));
    }
    return next;
  }

  return Object.freeze({
    STEP_COUNT,
    TB303_NOTES,
    NOTE_SCALES,
    NOTE_FIELDS,
    TRACKS,
    PATTERNS,
    clamp,
    blankSteps,
    createPreset,
    sanitizeState,
    toggleStep,
    cycleInstrumentNote,
    cycleTb303Note,
    randomizeState
  });
});
