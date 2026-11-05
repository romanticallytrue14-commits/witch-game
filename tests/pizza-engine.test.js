"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const E = require("../pizza-apocalypse/engine.js");

function emptyRun(seed = 42) {
  const s = E.startRun(seed);
  s.spawnTimer = 9999;
  return s;
}
function step(s, seconds, input = {}) {
  const events = [];
  for (let i = 0; i < Math.round(seconds * 60); i++) events.push(...E.update(s, input, 1 / 60));
  return events;
}

test("fresh state is ready, full and does not advance", () => {
  const s = E.createState(1);
  assert.equal(s.phase, "ready");
  assert.equal(s.health, 100); assert.equal(s.fuel, 100); assert.equal(s.nitro, 100);
  E.update(s, { fire: true, boost: true }, 1);
  assert.equal(s.time, 0); assert.equal(s.distance, 0); assert.equal(s.projectiles.length, 0);
});
test("steering clamps to four lanes and smoothly interpolates", () => {
  const s = emptyRun();
  E.steer(s, -1); assert.equal(s.player.lane, 0);
  E.steer(s, -1); assert.equal(s.player.lane, 0);
  E.steer(s, 1); E.steer(s, 1); E.steer(s, 1); E.steer(s, 1);
  assert.equal(s.player.lane, 3);
  step(s, 1); assert.ok(Math.abs(s.player.x - 3) < 0.001);
});
test("pause freezes simulation and resume continues", () => {
  const s = emptyRun(); step(s, 1);
  assert.equal(E.pause(s), true);
  const before = E.snapshot(s);
  step(s, 2, { fire: true, boost: true });
  assert.deepEqual(E.snapshot(s), before);
  assert.equal(E.steer(s, 1), false); assert.equal(E.fire(s), false);
  assert.equal(E.resume(s), true); step(s, 1);
  assert.ok(s.time > before.time);
});
test("fuel drains, distance and score increase, empty tank ends run", () => {
  const s = emptyRun(); step(s, 1);
  assert.ok(s.fuel < 100); assert.ok(s.distance > 20); assert.ok(s.score > 0);
  s.fuel = 0.01;
  const events = step(s, 0.1);
  assert.equal(s.phase, "over"); assert.equal(s.reason, "fuel");
  assert.ok(events.some((e) => e.type === "over"));
  const score = s.score; step(s, 1); assert.equal(s.score, score);
});
test("firing respects cooldown and pizza destroys a taxi", () => {
  const s = emptyRun();
  assert.equal(E.fire(s), true); assert.equal(E.fire(s), false);
  assert.equal(s.projectiles.length, 1);
  s.projectiles = []; s.shotCooldown = 0;
  const taxi = E.addEntity(s, "taxi", s.player.lane, 0.55);
  const events = step(s, 0.5, { fire: true });
  assert.ok(events.some((e) => e.type === "destroy" && e.typeOf === "taxi"));
  assert.equal(s.kills, 1); assert.ok(s.score >= 160); assert.ok(!s.entities.includes(taxi));
});
test("collision damages once, grants invulnerability and resets combo", () => {
  const s = emptyRun(); s.combo = 4; s.comboTimer = 3;
  E.addEntity(s, "taxi", s.player.lane, E.PLAYER_Z - 0.01);
  const events = step(s, 1 / 60);
  assert.equal(s.health, 72); assert.equal(s.combo, 1); assert.ok(s.invulnerable > 1);
  assert.ok(events.some((e) => e.type === "crash"));
  E.addEntity(s, "van", s.player.lane, E.PLAYER_Z);
  step(s, 1 / 60); assert.equal(s.health, 72);
});
test("zero health ends the run with wreck reason", () => {
  const s = emptyRun(); s.health = 10;
  E.addEntity(s, "cone", s.player.lane, E.PLAYER_Z);
  step(s, 1 / 60); assert.equal(s.phase, "over"); assert.equal(s.reason, "wreck");
});
test("nitro rams obstacles, drains and locks when empty", () => {
  const s = emptyRun();
  E.addEntity(s, "van", s.player.lane, E.PLAYER_Z);
  const events = step(s, 1 / 60, { boost: true });
  assert.equal(s.health, 100); assert.equal(s.kills, 1); assert.ok(s.nitro < 100);
  assert.ok(events.some((e) => e.type === "ram"));
  s.nitro = 0.1; step(s, 1 / 60, { boost: true });
  assert.equal(s.nitro, 0); assert.equal(s.boostActive, false); assert.ok(s.boostLock > 0);
  step(s, 1); assert.ok(s.nitro > 0);
});
test("all four pickups apply capped effects", () => {
  const s = emptyRun(); s.fuel = 50; s.health = 50; s.nitro = 50;
  for (const type of ["fuel", "repair", "nitro", "pizza"]) E.addEntity(s, type, s.player.lane, E.PLAYER_Z);
  const events = step(s, 1 / 60);
  assert.ok(Math.abs(s.fuel - 84) < 0.1); assert.ok(Math.abs(s.health - 80) < 0.1); assert.ok(Math.abs(s.nitro - 90) < 0.1); assert.ok(s.score >= 250);
  assert.equal(events.filter((e) => e.type === "pickup").length, 4);
  s.fuel = 99; s.health = 99; s.nitro = 99;
  for (const type of ["fuel", "repair", "nitro"]) E.addEntity(s, type, s.player.lane, E.PLAYER_Z);
  step(s, 1 / 60); assert.equal(s.fuel, 100); assert.equal(s.health, 100); assert.equal(s.nitro, 100);
});
test("projectiles do not destroy pickups", () => {
  const s = emptyRun(); const fuel = E.addEntity(s, "fuel", s.player.lane, 0.6);
  step(s, 0.3, { fire: true }); assert.ok(s.entities.includes(fuel));
});
test("near miss awards points and counts dodge", () => {
  const s = emptyRun(); E.addEntity(s, "cone", 2, E.PLAYER_Z + 0.09);
  const events = step(s, 1 / 60);
  assert.equal(s.dodged, 1); assert.ok(events.some((e) => e.type === "near"));
});
test("combo caps at five and expires", () => {
  const s = emptyRun();
  for (let i = 0; i < 8; i++) {
    s.shotCooldown = 0;
    E.addEntity(s, "cone", s.player.lane, E.PLAYER_Z - 0.075);
    step(s, 1 / 60, { fire: true });
  }
  assert.equal(s.combo, 5);
  step(s, 4.1); assert.equal(s.combo, 1);
});
test("district changes at 750 meters and emits an event", () => {
  const s = emptyRun(); s.distance = 749.9;
  const events = step(s, 0.1);
  assert.equal(s.level, 2); assert.equal(E.district(s).name, "ПРОМЗОНА МУТАНТОВ");
  assert.ok(events.some((e) => e.type === "level"));
});
test("spawn rows always leave a reachable safe lane", () => {
  const s = emptyRun();
  for (let i = 0; i < 500; i++) {
    const prev = s.lastSafeLane; s.entities = []; s.distance = i * 10;
    E.spawnRow(s);
    assert.ok(Math.abs(s.lastSafeLane - prev) <= 1);
    const obstacles = s.entities.filter((e) => e.kind === "obstacle");
    assert.ok(obstacles.length <= 3);
    assert.ok(!obstacles.some((e) => e.lane === s.lastSafeLane));
  }
});
test("deterministic seeds produce identical simulations", () => {
  const a = E.startRun(1234), b = E.startRun(1234);
  for (let i = 0; i < 1800; i++) {
    const input = { fire: i % 120 < 70, boost: i % 300 < 40 };
    if (i % 90 === 0) { E.steer(a, 1); E.steer(b, 1); }
    E.update(a, input, 1 / 60); E.update(b, input, 1 / 60);
  }
  assert.deepEqual(E.snapshot(a), E.snapshot(b));
  assert.deepEqual(a.entities, b.entities);
});
test("bad delta times cannot corrupt state", () => {
  const s = emptyRun();
  for (const dt of [NaN, Infinity, -1, undefined]) E.update(s, {}, dt);
  assert.equal(s.time, 0); assert.equal(s.fuel, 100);
  E.update(s, {}, 999); assert.ok(s.time <= 0.1);
});
test("restart returns a clean independent run", () => {
  const a = emptyRun(); step(a, 2); E.fire(a); E.steer(a, 1);
  const b = E.startRun(99);
  assert.equal(b.phase, "running"); assert.equal(b.score, 0); assert.equal(b.distance, 0);
  assert.equal(b.health, 100); assert.equal(b.entities.length, 0); assert.equal(b.projectiles.length, 0);
  assert.equal(b.player.lane, 1); assert.ok(a.projectiles.length > 0);
});
test("long unattended run eventually ends and remains bounded", () => {
  const s = E.startRun(777);
  for (let i = 0; i < 60 * 180 && s.phase === "running"; i++) E.update(s, {}, 1 / 60);
  assert.equal(s.phase, "over"); assert.ok(s.entities.length < 30); assert.ok(s.projectiles.length < 10);
  assert.ok(Number.isFinite(s.score)); assert.ok(s.health >= 0); assert.ok(s.fuel >= 0);
});
