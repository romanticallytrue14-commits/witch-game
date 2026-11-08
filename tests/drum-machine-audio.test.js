"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "..", "drum-machine");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8")
  .replace('<link rel="stylesheet" href="style.css">', "")
  .replace('<script src="engine.js"></script>', "<script>" + fs.readFileSync(path.join(root, "engine.js"), "utf8") + "</script>")
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

function build({ pointerEvents = true } = {}) {
  const contexts = [];
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

  const page = new JSDOM(html, {
    runScripts: "dangerously",
    url: "https://witchbeat.test/drum-machine/",
    beforeParse(window) {
      window.AudioContext = FakeAudioContext;
      window.PointerEvent = pointerEvents ? window.MouseEvent : undefined;
    }
  });
  const { window } = page;
  const button = window.document.getElementById("playButton");
  const dispatch = (type) => button.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true }));
  return { page, window, contexts, button, dispatch };
}

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve));

test("mobile pointer gesture resumes Web Audio and primes the output before playback", async () => {
  const app = build();
  try {
    app.dispatch("pointerdown");
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
