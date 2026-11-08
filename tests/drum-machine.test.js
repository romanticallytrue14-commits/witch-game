"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Engine = require("../drum-machine/engine.js");
const Wav = require("../drum-machine/wav.js");

const root = path.resolve(__dirname, "..", "drum-machine");
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");

test("each built-in groove has a complete 16-step row for every instrument", () => {
  assert.equal(Engine.TRACKS.length, 10);
  assert.equal(Object.keys(Engine.PATTERNS).length, 4);
  for (const id of Object.keys(Engine.PATTERNS)) {
    const state = Engine.createPreset(id);
    assert.equal(state.preset, id);
    assert.ok(state.tempo >= 60 && state.tempo <= 180);
    assert.equal(state.tb303Notes.length, 16);
    for (const [trackId, field] of Object.entries(Engine.NOTE_FIELDS)) {
      assert.equal(state[field].length, 16, `${trackId} notes should have 16 values`);
      assert.ok(state[field].every((note) => note >= 0 && note < Engine.NOTE_SCALES[trackId].length));
    }
    assert.ok(state.tb303Cutoff >= 250 && state.tb303Cutoff <= 8500);
    assert.ok(state.tb303Resonance >= 0 && state.tb303Resonance <= 100);
    for (const track of Engine.TRACKS) {
      assert.equal(state.steps[track.id].length, 16);
      assert.ok(state.steps[track.id].every((step) => typeof step === "boolean"));
      assert.ok(state.steps[track.id].some(Boolean), `${id}/${track.id} should have a step`);
    }
  }
});

test("every built-in groove gives the string lane a multi-note melody", () => {
  for (const presetId of Object.keys(Engine.PATTERNS)) {
    const state = Engine.createPreset(presetId);
    const steps = state.steps.strings.flatMap((active, step) => active ? [step] : []);
    const notes = steps.map((step) => state.stringsNotes[step]);
    assert.ok(steps.length >= 3, `${presetId} should contain several string notes`);
    assert.ok(new Set(notes).size >= 2, `${presetId} should use different string pitches`);
  }
});

test("sanitization clamps settings, strips unknown tracks, and never mutates input", () => {
  const raw = {
    preset: "missing",
    tempo: 999,
    swing: -25,
    volume: 4,
    steps: { kick: [true, false, "true", 1], unknown: Array(16).fill(true) }
  };
  const before = structuredClone(raw);
  const clean = Engine.sanitizeState(raw);
  assert.deepEqual(raw, before);
  assert.equal(clean.preset, "custom");
  assert.equal(clean.tempo, 180);
  assert.equal(clean.swing, 0);
  assert.equal(clean.volume, 1);
  assert.deepEqual(clean.steps.kick.slice(0, 4), [true, false, false, false]);
  assert.equal(Object.hasOwn(clean.steps, "unknown"), false);
  assert.equal(clean.steps.snare.length, 16);
});

test("broken saved data falls back safely and numeric strings are not trusted", () => {
  const clean = Engine.sanitizeState({ preset: "pulse", tempo: "144", swing: NaN, steps: null });
  assert.equal(clean.preset, "pulse");
  assert.equal(clean.tempo, 132);
  assert.equal(clean.swing, 0);
  assert.equal(clean.steps.kick.some(Boolean), true);
  assert.equal(Engine.sanitizeState(null).preset, "midnight");
  assert.equal(Engine.sanitizeState([]).preset, "midnight");
});

test("toggling a step returns an independent custom pattern", () => {
  const original = Engine.createPreset("midnight");
  const changed = Engine.toggleStep(original, "kick", 0);
  assert.equal(original.steps.kick[0], true);
  assert.equal(changed.steps.kick[0], false);
  assert.equal(changed.preset, "custom");
  assert.notEqual(changed.steps.kick, original.steps.kick);
  assert.equal(Engine.toggleStep(changed, "missing", 4), changed);
  assert.equal(Engine.toggleStep(changed, "kick", 16), changed);
});

test("TB-303 long-press cycles a note and activates its step", () => {
  const original = Engine.createPreset("midnight");
  const inactiveStep = 1;
  const changed = Engine.cycleTb303Note(original, inactiveStep);
  assert.equal(original.steps.tb303[inactiveStep], false);
  assert.equal(changed.steps.tb303[inactiveStep], true);
  assert.equal(changed.tb303Notes[inactiveStep], (original.tb303Notes[inactiveStep] + 1) % Engine.TB303_NOTES.length);
  assert.equal(changed.preset, "custom");
  assert.equal(Engine.cycleTb303Note(changed, 16), changed);
});

test("piano, organ, strings and bell steps cycle notes in their own ranges", () => {
  const original = Engine.createPreset("midnight");
  for (const trackId of ["piano", "organ", "strings", "bell"]) {
    const field = Engine.NOTE_FIELDS[trackId];
    const changed = Engine.cycleInstrumentNote(original, trackId, 1);
    assert.equal(changed.steps[trackId][1], true);
    assert.equal(changed[field][1], (original[field][1] + 1) % Engine.NOTE_SCALES[trackId].length);
    assert.equal(original.steps[trackId][1], false);
  }
});

test("legacy five-drum saves migrate without losing grooves or adding new lanes silently", () => {
  const legacy = Engine.createPreset("broken");
  for (const trackId of ["tb303", "piano", "organ", "strings", "bell"]) delete legacy.steps[trackId];
  for (const field of Object.values(Engine.NOTE_FIELDS)) delete legacy[field];
  delete legacy.tb303Cutoff;
  delete legacy.tb303Resonance;
  const clean = Engine.sanitizeState(legacy);
  assert.deepEqual(clean.steps.kick, legacy.steps.kick);
  for (const trackId of ["tb303", "piano", "organ", "strings", "bell"]) {
    assert.ok(clean.steps[trackId].some(Boolean), `${trackId} default lane should migrate`);
  }
  assert.equal(clean.tb303Notes.length, 16);
  assert.ok(clean.tb303Cutoff > 0);
});

test("v2.2 six-track saves keep their groove and receive four new instrument lanes", () => {
  const legacy = Engine.createPreset("pulse");
  for (const trackId of ["piano", "organ", "strings", "bell"]) {
    delete legacy.steps[trackId];
    delete legacy[Engine.NOTE_FIELDS[trackId]];
  }
  const clean = Engine.sanitizeState(legacy);
  for (const trackId of ["kick", "snare", "hatClosed", "hatOpen", "clap", "tb303"]) {
    assert.deepEqual(clean.steps[trackId], legacy.steps[trackId]);
  }
  assert.deepEqual(clean.tb303Notes, legacy.tb303Notes);
  for (const trackId of ["piano", "organ", "strings", "bell"]) {
    assert.ok(clean.steps[trackId].some(Boolean), `${trackId} lane should be added from its preset`);
    assert.equal(clean[Engine.NOTE_FIELDS[trackId]].length, 16);
  }
});

test("untouched saved presets migrate their old string rows to the new melodies", () => {
  const legacySteps = { midnight: [0, 8], broken: [1, 9], halfTime: [0, 8], pulse: [0, 8] };
  const oldNotes = [0, 0, 0, 0, 3, 0, 0, 0, 5, 0, 0, 0, 3, 0, 0, 0];
  for (const presetId of Object.keys(Engine.PATTERNS)) {
    const legacy = Engine.createPreset(presetId);
    legacy.steps.strings = Array(16).fill(false);
    for (const step of legacySteps[presetId]) legacy.steps.strings[step] = true;
    legacy.stringsNotes = oldNotes.slice();
    const migrated = Engine.sanitizeState(legacy);
    const fresh = Engine.createPreset(presetId);
    assert.deepEqual(migrated.steps.strings, fresh.steps.strings);
    assert.deepEqual(migrated.stringsNotes, fresh.stringsNotes);
  }

  const custom = Engine.createPreset("midnight");
  custom.preset = "custom";
  custom.steps.strings = Array(16).fill(false);
  custom.steps.strings[2] = true;
  custom.stringsNotes = oldNotes.slice();
  const preserved = Engine.sanitizeState(custom);
  assert.equal(preserved.steps.strings[2], true);
  assert.deepEqual(preserved.stringsNotes, oldNotes);
});

test("randomizer is deterministic with an injected source and preserves mix settings", () => {
  const original = Engine.createPreset("broken");
  original.volume = 0.42;
  const empty = Engine.randomizeState(original, () => 1);
  assert.equal(empty.preset, "custom");
  assert.equal(empty.tempo, original.tempo);
  assert.equal(empty.volume, 0.42);
  assert.ok(Engine.TRACKS.every((track) => empty.steps[track.id].every((step) => step === false)));
  const full = Engine.randomizeState(original, () => 0);
  assert.ok(Engine.TRACKS.every((track) => full.steps[track.id].every((step) => step === true)));
  assert.equal(original.preset, "broken");
});

test("WAV encoder writes little-endian stereo PCM16 samples", () => {
  const audioBuffer = {
    numberOfChannels: 2,
    length: 2,
    sampleRate: 48000,
    getChannelData(channel) {
      return channel === 0 ? Float32Array.from([1, -1]) : Float32Array.from([0.5, -0.5]);
    }
  };
  const encoded = Wav.encodeWav(audioBuffer);
  const bytes = Buffer.from(encoded);
  const view = new DataView(encoded);
  assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
  assert.equal(bytes.toString("ascii", 8, 12), "WAVE");
  assert.equal(bytes.toString("ascii", 36, 40), "data");
  assert.equal(view.getUint32(24, true), 48000);
  assert.equal(view.getUint16(22, true), 2);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 8);
  assert.deepEqual([view.getInt16(44, true), view.getInt16(46, true), view.getInt16(48, true), view.getInt16(50, true)], [32767, 16383, -32768, -16384]);
});

test("PWA metadata, install icons, app assets and offline cache are complete", () => {
  const manifest = JSON.parse(read("manifest.webmanifest"));
  assert.equal(manifest.lang, "ru");
  assert.equal(manifest.display, "standalone");
  assert.equal(manifest.orientation, "portrait");
  assert.equal(manifest.start_url, "./");
  assert.equal(manifest.scope, "./");
  for (const icon of manifest.icons) {
    const buffer = fs.readFileSync(path.join(root, icon.src));
    assert.equal(buffer.subarray(1, 4).toString(), "PNG");
    const [width, height] = icon.sizes.split("x").map(Number);
    assert.equal(buffer.readUInt32BE(16), width);
    assert.equal(buffer.readUInt32BE(20), height);
  }
  const apple = fs.readFileSync(path.join(root, "apple-touch-icon.png"));
  assert.equal(apple.readUInt32BE(16), 180);
  assert.equal(apple.readUInt32BE(20), 180);

  const html = read("index.html");
  assert.match(html, /rel="manifest" href="manifest\.webmanifest"/);
  assert.match(html, /rel="apple-touch-icon" href="apple-touch-icon\.png"/);
  assert.match(html, /id="playButton"/);
  assert.match(html, /id="beatRuler"/);
  assert.match(html, /1 ДОЛЯ = 4 ШАГА/);
  assert.match(html, /tbCutoffRange/);
  assert.match(html, /tbResonanceRange/);
  assert.match(html, /id="exportLink"/);
  assert.match(html, /<b>10<\/b> дорожек/);
  assert.doesNotMatch(html, /page-switch|data-page/);
  assert.match(html, /id="trackRows"/);
  assert.match(html, /id="installDialog"/);
  assert.match(html, /<script src="engine\.js"><\/script>/);
  assert.match(html, /<script src="wav\.js"><\/script>/);
  assert.match(html, /<script src="app\.js"><\/script>/);

  const sw = read("sw.js");
  assert.match(sw, /const CACHE_PREFIX = "witchbeat-"/);
  assert.match(sw, /const CACHE_NAME = "witchbeat-v3\.1\.0"/);
  for (const asset of ["index.html", "style.css", "engine.js", "wav.js", "app.js", "manifest.webmanifest", "apple-touch-icon.png"]) {
    assert.match(sw, new RegExp("\\./" + asset.replaceAll(".", "\\.")));
    assert.ok(fs.existsSync(path.join(root, asset)));
  }
  assert.match(sw, /request\.mode === "navigate"/);
});

test("browser scripts parse and the app has no remote runtime dependency", () => {
  assert.doesNotThrow(() => new Function(read("engine.js")));
  assert.doesNotThrow(() => new Function(read("wav.js")));
  assert.doesNotThrow(() => new Function(read("app.js")));
  const html = read("index.html");
  assert.doesNotMatch(html, /<script[^>]+src=["']https?:/i);
  assert.doesNotMatch(html, /<link[^>]+href=["']https?:/i);
});
