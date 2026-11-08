"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const Engine = require("../drum-machine/engine.js");

const root = path.resolve(__dirname, "..", "drum-machine");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8")
  .replace('<link rel="stylesheet" href="style.css">', "")
  .replace('<script src="engine.js"></script>', "<script>" + fs.readFileSync(path.join(root, "engine.js"), "utf8") + "</script>")
  .replace('<script src="wav.js"></script>', "<script>" + fs.readFileSync(path.join(root, "wav.js"), "utf8") + "</script>")
  .replace('<script src="app.js"></script>', "<script>" + fs.readFileSync(path.join(root, "app.js"), "utf8") + "</script>");

function makeParam() {
  return {
    value: 0,
    setValueAtTime(value) { this.value = value; },
    exponentialRampToValueAtTime(value) { this.value = value; },
    linearRampToValueAtTime(value) { this.value = value; },
    setTargetAtTime(value) { this.value = value; },
    cancelScheduledValues() {}
  };
}

function build({ pointerEvents = true, audioSession = null, storage = {}, offlineAudio = false } = {}) {
  const contexts = [];
  const createdUrls = [];
  class FakeAudioContext {
    constructor() {
      this.state = "suspended";
      this.currentTime = 0;
      this.sampleRate = 44100;
      this.destination = {};
      this.resumeCalls = 0;
      this.bufferStarts = [];
      contexts.push(this);
    }
    resume() {
      this.resumeCalls++;
      this.state = "running";
      return Promise.resolve();
    }
    createGain() {
      return { gain: makeParam(), connect() {} };
    }
    createDynamicsCompressor() {
      return {
        threshold: makeParam(), knee: makeParam(), ratio: makeParam(),
        attack: makeParam(), release: makeParam(), connect() {}
      };
    }
    createBuffer(_channels, length) {
      const samples = new Float32Array(length);
      return { getChannelData: () => samples, samples };
    }
    createBufferSource() {
      const source = {
        buffer: null,
        connect() {},
        start: (time) => this.bufferStarts.push({ buffer: source.buffer, time }),
        stop() {}
      };
      return source;
    }
    createOscillator() {
      return { type: "sine", frequency: makeParam(), connect() {}, start() {}, stop() {} };
    }
    createBiquadFilter() {
      return { type: "lowpass", frequency: makeParam(), Q: makeParam(), connect() {} };
    }
  }
  class FakeOfflineAudioContext extends FakeAudioContext {
    constructor(channels, length, sampleRate) {
      super();
      this.numberOfChannels = channels;
      this.length = length;
      this.sampleRate = sampleRate;
    }
    startRendering() {
      return Promise.resolve({
        numberOfChannels: 2,
        length: 4,
        sampleRate: this.sampleRate,
        getChannelData(channel) {
          return Float32Array.from(channel === 0 ? [0, 0.25, -0.25, 1] : [0, -0.25, 0.25, -1]);
        }
      });
    }
  }

  const page = new JSDOM(html, {
    runScripts: "dangerously",
    url: "https://witchbeat.test/drum-machine/",
    beforeParse(window) {
      window.AudioContext = FakeAudioContext;
      window.OfflineAudioContext = offlineAudio ? FakeOfflineAudioContext : undefined;
      window.webkitOfflineAudioContext = undefined;
      window.URL.createObjectURL = (blob) => {
        createdUrls.push(blob);
        return `blob:witchbeat-${createdUrls.length}`;
      };
      window.URL.revokeObjectURL = () => {};
      window.PointerEvent = pointerEvents ? window.MouseEvent : undefined;
      if (audioSession) Object.defineProperty(window.navigator, "audioSession", { value: audioSession, configurable: true });
      for (const [key, value] of Object.entries(storage)) window.localStorage.setItem(key, value);
    }
  });
  const { window } = page;
  const button = window.document.getElementById("playButton");
  const dispatch = (type) => button.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true }));
  return { page, window, contexts, createdUrls, button, dispatch };
}

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve));

test("mobile pointer gesture selects iOS playback audio and primes output before playback", async () => {
  const audioSession = { type: "ambient" };
  const app = build({ audioSession });
  try {
    app.dispatch("pointerdown");
    assert.equal(audioSession.type, "playback");
    assert.equal(app.contexts.length, 1);
    const context = app.contexts[0];
    assert.equal(context.resumeCalls, 1);
    assert.equal(context.state, "running");
    assert.equal(context.bufferStarts.length, 1, "a silent buffer should warm up the mobile audio route");
    assert.ok(context.bufferStarts[0].buffer.samples.every((sample) => sample === 0));

    app.dispatch("click");
    await flushMicrotasks();
    assert.equal(app.window.document.getElementById("playState").textContent, "ИГРАЕТ");
    app.dispatch("click");
    assert.equal(app.window.document.getElementById("playState").textContent, "ГОТОВ");
  } finally {
    app.page.window.close();
  }
});

test("click-only fallback still unlocks audio on browsers without Pointer Events", async () => {
  const app = build({ pointerEvents: false });
  try {
    app.dispatch("click");
    await flushMicrotasks();
    assert.equal(app.contexts.length, 1);
    assert.equal(app.contexts[0].resumeCalls, 1);
    assert.equal(app.contexts[0].state, "running");
    assert.ok(app.contexts[0].bufferStarts.length >= 1);
    assert.ok(app.contexts[0].bufferStarts[0].buffer.samples.every((sample) => sample === 0));
    assert.equal(app.window.document.getElementById("playState").textContent, "ИГРАЕТ");
  } finally {
    app.page.window.close();
  }
});

test("a closed audio context is recreated on the next user gesture", async () => {
  const app = build();
  try {
    app.dispatch("pointerdown");
    await flushMicrotasks();
    assert.equal(app.contexts.length, 1);
    app.contexts[0].state = "closed";
    app.dispatch("pointerdown");
    assert.equal(app.contexts.length, 2);
    assert.equal(app.contexts[1].resumeCalls, 1);
  } finally {
    app.page.window.close();
  }
});

test("an empty saved pattern explains why playback would be silent", async () => {
  const empty = Engine.createPreset("midnight");
  empty.preset = "custom";
  empty.steps = Engine.blankSteps();
  const app = build({ storage: { "witchbeat-pattern-v1": JSON.stringify(empty) } });
  try {
    app.dispatch("click");
    await flushMicrotasks();
    assert.equal(app.contexts.length, 0);
    assert.equal(app.window.document.getElementById("playState").textContent, "ГОТОВ");
    assert.match(app.window.document.getElementById("toast").textContent, /Сетка пустая/);
  } finally {
    app.page.window.close();
  }
});

test("zero app volume explains silence before playback starts", async () => {
  const muted = Engine.createPreset("midnight");
  muted.volume = 0;
  const app = build({ storage: { "witchbeat-pattern-v1": JSON.stringify(muted) } });
  try {
    app.dispatch("click");
    await flushMicrotasks();
    assert.equal(app.contexts.length, 0);
    assert.equal(app.window.document.getElementById("playState").textContent, "ГОТОВ");
    assert.match(app.window.document.getElementById("toast").textContent, /Громкость приложения на нуле/);
  } finally {
    app.page.window.close();
  }
});

test("the sequencer renders all ten instrument lanes with sixteen steps each", () => {
  const app = build();
  try {
    const rows = [...app.window.document.querySelectorAll("#trackRows .track-row")];
    assert.equal(rows.length, 10);
    assert.deepEqual(rows.map((row) => row.dataset.track), Engine.TRACKS.map((track) => track.id));
    for (const row of rows) assert.equal(row.querySelectorAll(".step-toggle").length, 16);
  } finally {
    app.page.window.close();
  }
});

test("holding a new instrument step selects a note without toggling it back off", async () => {
  const empty = Engine.createPreset("midnight");
  empty.preset = "custom";
  empty.steps = Engine.blankSteps();
  const app = build({ storage: { "witchbeat-pattern-v1": JSON.stringify(empty) } });
  try {
    const cell = app.window.document.querySelector('[data-track="piano"][data-step="3"]');
    assert.equal(cell.getAttribute("aria-pressed"), "false");
    cell.dispatchEvent(new app.window.MouseEvent("pointerdown", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 550));
    assert.equal(cell.getAttribute("aria-pressed"), "true");
    const nextNote = (empty.pianoNotes[3] + 1) % Engine.NOTE_SCALES.piano.length;
    assert.equal(cell.textContent, Engine.NOTE_SCALES.piano[nextNote].short);
    cell.dispatchEvent(new app.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    assert.equal(cell.getAttribute("aria-pressed"), "true");
  } finally {
    app.page.window.close();
  }
});

test("WAV export renders the current loop and prepares a downloadable audio file", async () => {
  const app = build({ offlineAudio: true });
  try {
    const link = app.window.document.getElementById("exportLink");
    const firstTap = new app.window.MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(firstTap);
    assert.equal(firstTap.defaultPrevented, true, "first tap should prepare the file for iOS-safe download");
    await flushMicrotasks();
    assert.equal(app.contexts.length, 1, "one offline renderer should be created");
    assert.equal(app.createdUrls.length, 1);
    assert.equal(app.createdUrls[0].type, "audio/wav");
    assert.ok(app.createdUrls[0].size > 44);
    assert.equal(link.download, "witchbeat-midnight-4bars.wav");
    assert.equal(link.classList.contains("is-ready"), true);
    assert.equal(app.window.document.getElementById("exportLabel").textContent, "СКАЧАТЬ WAV");
  } finally {
    app.page.window.close();
  }
});
