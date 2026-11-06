"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Engine = require("../drum-machine/engine.js");

const root = path.resolve(__dirname, "..", "drum-machine");
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");

test("each built-in groove has a complete 16-step row for every drum voice", () => {
  assert.equal(Engine.TRACKS.length, 5);
  assert.equal(Object.keys(Engine.PATTERNS).length, 4);
  for (const id of Object.keys(Engine.PATTERNS)) {
    const state = Engine.createPreset(id);
    assert.equal(state.preset, id);
    assert.ok(state.tempo >= 60 && state.tempo <= 180);
    for (const track of Engine.TRACKS) {
      assert.equal(state.steps[track.id].length, 16);
      assert.ok(state.steps[track.id].every((step) => typeof step === "boolean"));
      assert.ok(state.steps[track.id].some(Boolean), `${id}/${track.id} should have a step`);
    }
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
  assert.match(html, /id="trackRows"/);
  assert.match(html, /id="installDialog"/);
  assert.match(html, /<script src="engine\.js"><\/script>/);
  assert.match(html, /<script src="app\.js"><\/script>/);

  const sw = read("sw.js");
  assert.match(sw, /const CACHE_PREFIX = "witchbeat-"/);
  for (const asset of ["index.html", "style.css", "engine.js", "app.js", "manifest.webmanifest", "apple-touch-icon.png"]) {
    assert.match(sw, new RegExp("\\./" + asset.replaceAll(".", "\\.")));
    assert.ok(fs.existsSync(path.join(root, asset)));
  }
  assert.match(sw, /request\.mode === "navigate"/);
});

test("browser scripts parse and the app has no remote runtime dependency", () => {
  assert.doesNotThrow(() => new Function(read("engine.js")));
  assert.doesNotThrow(() => new Function(read("app.js")));
  const html = read("index.html");
  assert.doesNotMatch(html, /<script[^>]+src=["']https?:/i);
  assert.doesNotMatch(html, /<link[^>]+href=["']https?:/i);
});
