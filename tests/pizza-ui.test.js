"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "..", "pizza-apocalypse");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const engineJs = fs.readFileSync(path.join(root, "engine.js"), "utf8");
const gameJs = fs.readFileSync(path.join(root, "game.js"), "utf8");

function build(storage = {}) {
  const page = html
    .replace('<link rel="stylesheet" href="style.css">', "")
    .replace('<script src="engine.js"></script>', "<script>" + engineJs + "</script>")
    .replace('<script src="game.js"></script>', "<script>" + gameJs + "</script>");
  const dom = new JSDOM(page, {
    runScripts: "dangerously",
    url: "https://pizza.test/",
    beforeParse(window) {
      window.Math.random = () => 0.42;
      window.HTMLCanvasElement.prototype.getContext = function () {
        const grad = { addColorStop() {} };
        return new Proxy({}, { get: (t, p) => p === "canvas" ? this : (...a) => String(p).startsWith("create") ? grad : undefined });
      };
      window.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({ width: 1000, height: 620, top: 0, left: 0 });
      window.ResizeObserver = class { observe() {} disconnect() {} };
      window.matchMedia = () => ({ matches: true, addEventListener() {} });
      window.requestAnimationFrame = (cb) => { window.__frame = cb; return 1; };
      window.cancelAnimationFrame = () => {};
      for (const [k, v] of Object.entries(storage)) window.localStorage.setItem(k, v);
    }
  });
  const win = dom.window;
  const $ = (id) => win.document.getElementById(id);
  const click = (id) => $(id).dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true, detail: 0 }));
  const key = (code, type = "keydown", repeat = false) => win.document.body.dispatchEvent(new win.KeyboardEvent(type, { code, bubbles: true, cancelable: true, repeat }));
  const frames = (n, step = 100) => { for (let i = 0; i < n; i++) { const cb = win.__frame; win.__frame = null; cb((win.__t = (win.__t || 0) + step)); } };
  const store = (k) => win.localStorage.getItem(k);
  return { dom, win, $, click, key, frames, store };
}

test("initial screen, HUD and disabled controls", () => {
  const { $, win } = build();
  assert.equal(win.PizzaGame.snapshot().phase, "ready");
  assert.equal($("start-screen").hidden, false);
  assert.equal($("pause").disabled, true);
  assert.equal($("fire").disabled, true);
  assert.equal($("health-value").textContent, "100%");
  assert.equal($("score").textContent, "000000");
});

test("start button begins run and enables controls", () => {
  const { $, click, win } = build();
  click("start");
  assert.equal(win.PizzaGame.snapshot().phase, "running");
  assert.equal($("start-screen").hidden, true);
  assert.equal($("fire").disabled, false);
  assert.equal($("pause").disabled, false);
});

test("physical keyboard works with Russian layout codes", () => {
  const { click, key, frames, win } = build();
  click("start");
  key("KeyD"); frames(10); key("KeyD", "keyup");
  key("Space"); frames(5);
  assert.ok(win.PizzaGame.snapshot().shots > 0);
  key("Space", "keyup");
  key("ShiftLeft"); frames(5);
  assert.ok(win.PizzaGame.snapshot().nitro < 100);
  key("ShiftLeft", "keyup");
});

test("pause freezes snapshot and resume continues", () => {
  const { click, key, frames, win } = build();
  click("start"); frames(10);
  key("KeyP");
  const paused = win.PizzaGame.snapshot();
  assert.equal(paused.phase, "paused");
  frames(20);
  assert.deepEqual(win.PizzaGame.snapshot(), paused);
  click("resume"); frames(10);
  assert.equal(win.PizzaGame.snapshot().phase, "running");
  assert.ok(win.PizzaGame.snapshot().time > paused.time);
});

test("visibility loss pauses and clears held input", () => {
  const { click, key, frames, win } = build();
  click("start"); key("Space"); frames(3);
  Object.defineProperty(win.document, "hidden", { configurable: true, value: true });
  win.document.dispatchEvent(new win.Event("visibilitychange"));
  assert.equal(win.PizzaGame.snapshot().phase, "paused");
  const before = win.PizzaGame.snapshot().shots;
  frames(10);
  assert.equal(win.PizzaGame.snapshot().shots, before);
});

test("accessibility click on fire shoots once", () => {
  const { click, win } = build();
  click("start"); click("fire");
  assert.equal(win.PizzaGame.snapshot().shots, 1);
});

test("garage returns to clean ready state", () => {
  const { click, frames, win, $ } = build();
  click("start"); frames(20); click("pause"); click("pause-restart"); frames(5);
  //Force a game over by waiting without steering or collecting.
  for (let i = 0; i < 2000 && win.PizzaGame.snapshot().phase === "running"; i++) frames(1);
  assert.equal(win.PizzaGame.snapshot().phase, "over");
  click("garage");
  assert.equal(win.PizzaGame.snapshot().phase, "ready");
  assert.equal($("start-screen").hidden, false);
  assert.equal(win.PizzaGame.snapshot().score, 0);
});

test("game over saves a record and restart clears the run", () => {
  const { click, frames, win, $, store } = build();
  click("start");
  for (let i = 0; i < 2000 && win.PizzaGame.snapshot().phase === "running"; i++) frames(1);
  assert.equal(win.PizzaGame.snapshot().phase, "over");
  assert.equal($("over-screen").hidden, false);
  const saved = JSON.parse(store("pizzaApocalypse.records.v1"));
  assert.equal(saved.length, 1);
  assert.ok(saved[0].score > 0);
  assert.ok(Number($("best").textContent.replace(/\D/g, "")) > 0);
  click("restart");
  assert.equal(win.PizzaGame.snapshot().phase, "running");
  assert.equal(win.PizzaGame.snapshot().score, 0);
  assert.equal(win.PizzaGame.snapshot().health, 100);
});

test("corrupt records are ignored and malformed dates are sanitized", () => {
  const { win, $ } = build({ "pizzaApocalypse.records.v1": JSON.stringify([
    { score: "bad", distance: 1, kills: 0 },
    { score: 100, distance: 50, kills: 2, date: "<script>x</script>extra" },
    null
  ]) });
  assert.equal($("best").textContent, "100");
  assert.equal($("records").children.length, 1);
  assert.ok(!$("records").innerHTML.includes("<script"));
  assert.equal(win.PizzaGame.snapshot().phase, "ready");
});

test("non-array records storage does not crash", () => {
  const { win } = build({ "pizzaApocalypse.records.v1": '{"oops":true}' });
  assert.equal(win.PizzaGame.snapshot().phase, "ready");
});

test("sound preference is restored but audio waits for a gesture", () => {
  const { $, click, store } = build({ "pizzaApocalypse.sound": "1" });
  assert.equal($("sound").getAttribute("aria-pressed"), "true");
  click("sound");
  assert.equal($("sound").getAttribute("aria-pressed"), "false");
  assert.equal(store("pizzaApocalypse.sound"), "0");
});

test("Enter starts from ready and restarts from over", () => {
  const { key, frames, win } = build();
  key("Enter");
  assert.equal(win.PizzaGame.snapshot().phase, "running");
  for (let i = 0; i < 2000 && win.PizzaGame.snapshot().phase === "running"; i++) frames(1);
  assert.equal(win.PizzaGame.snapshot().phase, "over");
  key("Enter");
  assert.equal(win.PizzaGame.snapshot().phase, "running");
});
