/* Пицца апокалипсиса — чистая игровая логика. Без DOM, чтобы тестировать в Node. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PizzaEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const LANES = 4;
  const PLAYER_Z = 0.82;
  const MAX_HEALTH = 100;
  const MAX_FUEL = 100;
  const MAX_NITRO = 100;
  const SHOT_COOLDOWN = 0.24;
  const DISTRICT_LENGTH = 750;

  const DISTRICTS = [
    { name: "СПАЛЬНЫЙ АД", sky: "#2b1b3a", ground: "#3a2430", road: "#23252d", accent: "#ff624d" },
    { name: "ПРОМЗОНА МУТАНТОВ", sky: "#122b33", ground: "#1d3a38", road: "#20282b", accent: "#65d6c3" },
    { name: "ШОССЕ БЕЗ НАДЕЖДЫ", sky: "#33203b", ground: "#402a35", road: "#28242e", accent: "#f5df4d" },
    { name: "ПОСЛЕДНИЙ СЪЕЗД", sky: "#1a1a2e", ground: "#2c2438", road: "#22222c", accent: "#ff8a5c" }
  ];

  const OBSTACLES = {
    cone: { hp: 1, damage: 18, score: 40, name: "Конус судьбы" },
    taxi: { hp: 2, damage: 28, score: 80, name: "Зомби-такси" },
    barrier: { hp: 3, damage: 34, score: 120, name: "Ремонт навсегда" },
    van: { hp: 4, damage: 40, score: 160, name: "Бронированная бухгалтерия" }
  };

  const PICKUPS = {
    fuel: { name: "Канистра", text: "Полный бак. Полное отсутствие осторожности." },
    repair: { name: "Изолента", text: "Изолента спасает мир. +30 прочности." },
    nitro: { name: "Нитро", text: "Жидкая безответственность. +40 нитро." },
    pizza: { name: "Чаевые", text: "Чаевые пиццей. Логистика сломалась. +250 очков." }
  };

  const KILL_QUOTES = [
    "Пять звёзд. За смелость.",
    "Он и так был мёртвый.",
    "Доставка в морг отменяется.",
    "Чаевые не предусмотрены.",
    "Пепперони против зомби: 1:0.",
    "Клиент недоволен. Клиент — зомби."
  ];

  const CRASH_QUOTES = [
    "Бампер всё. Самооценка тоже.",
    "Это был не поворотник. Это был крик души.",
    "Страховка не покрывает апокалипсис.",
    "Коробка цела. Вопросы к водителю."
  ];

  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  function makeRng(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function createState(seed) {
    const s = {
      phase: "ready",
      seed: seed >>> 0,
      rng: makeRng(seed),
      time: 0,
      distance: 0,
      score: 0,
      speed: 0,
      health: MAX_HEALTH,
      fuel: MAX_FUEL,
      nitro: MAX_NITRO,
      combo: 1,
      comboTimer: 0,
      kills: 0,
      dodged: 0,
      shots: 0,
      level: 1,
      roadOffset: 0,
      spawnTimer: 0.8,
      pickupCounter: 0,
      shotCooldown: 0,
      invulnerable: 0,
      boostActive: false,
      boostLock: 0,
      player: { lane: 1, x: 1 },
      entities: [],
      projectiles: [],
      nextId: 1,
      lastSafeLane: 1,
      reason: null
    };
    return s;
  }

  function startRun(seed) {
    const s = createState(seed);
    s.phase = "running";
    s.speed = 105;
    return s;
  }

  function steer(s, direction) {
    if (s.phase !== "running") return false;
    const next = clamp(s.player.lane + Math.sign(direction || 0), 0, LANES - 1);
    if (next === s.player.lane) return false;
    s.player.lane = next;
    return true;
  }

  function pause(s) { if (s.phase === "running") { s.phase = "paused"; s.boostActive = false; return true; } return false; }
  function resume(s) { if (s.phase === "paused") { s.phase = "running"; return true; } return false; }

  function district(s) { return DISTRICTS[(s.level - 1) % DISTRICTS.length]; }

  function addEntity(s, type, lane, z) {
    const def = OBSTACLES[type];
    const e = {
      id: s.nextId++, kind: def ? "obstacle" : "pickup", type, lane,
      x: lane, z, hp: def ? def.hp : 1, maxHp: def ? def.hp : 1,
      passed: false, hit: false, wobble: s.rng() * Math.PI * 2
    };
    s.entities.push(e);
    return e;
  }

  function spawnRow(s) {
    //Safe lane moves at most one step, so a reachable escape always exists.
    const drift = Math.floor(s.rng() * 3) - 1;
    const safe = clamp(s.lastSafeLane + drift, 0, LANES - 1);
    s.lastSafeLane = safe;
    const candidates = [0, 1, 2, 3].filter((l) => l !== safe);
    // Shuffle.
    for (let i = candidates.length - 1; i > 0; i--) {
      const j = Math.floor(s.rng() * (i + 1));
      [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
    }
    const count = s.distance < 400 ? 1 : s.distance < 1400 ? 2 : 3;
    for (let i = 0; i < count; i++) {
      const r = s.rng();
      let type = "cone";
      if (s.distance > 250 && r > 0.45) type = "taxi";
      if (s.distance > 800 && r > 0.72) type = "barrier";
      if (s.distance > 1600 && r > 0.88) type = "van";
      addEntity(s, type, candidates[i], -0.08 - i * 0.015);
    }

    s.pickupCounter++;
    const wantsPickup = s.pickupCounter % 4 === 0 || s.rng() < 0.3;
    if (wantsPickup) {
      let type;
      const r = s.rng();
      if (s.pickupCounter % 4 === 0 || s.fuel < 40) type = "fuel";
      else if (s.health < 55 && r < 0.5) type = "repair";
      else if (r < 0.4) type = "fuel";
      else if (r < 0.62) type = "repair";
      else if (r < 0.88) type = "nitro";
      else type = "pizza";
      addEntity(s, type, safe, -0.3);
    }
  }

  function fire(s) {
    if (s.phase !== "running" || s.shotCooldown > 0) return false;
    s.shotCooldown = SHOT_COOLDOWN;
    s.shots++;
    s.projectiles.push({ id: s.nextId++, x: s.player.x, lane: s.player.lane, z: PLAYER_Z - 0.07, spin: 0 });
    return true;
  }

  function destroyObstacle(s, e, events, ram) {
    e.hit = true;
    s.kills++;
    s.combo = Math.min(5, s.combo + 1);
    s.comboTimer = 4;
    const base = OBSTACLES[e.type].score;
    const points = Math.round(base * s.combo * (ram ? 1.5 : 1));
    s.score += points;
    events.push({
      type: ram ? "ram" : "destroy", typeOf: e.type, x: e.x, z: e.z, points,
      text: ram ? "Экспресс-доставка!" : KILL_QUOTES[Math.floor(s.rng() * KILL_QUOTES.length)]
    });
  }

  function collect(s, e, events) {
    e.hit = true;
    let text = PICKUPS[e.type].text;
    if (e.type === "fuel") s.fuel = clamp(s.fuel + 28, 0, MAX_FUEL);
    if (e.type === "repair") s.health = clamp(s.health + 30, 0, MAX_HEALTH);
    if (e.type === "nitro") s.nitro = clamp(s.nitro + 40, 0, MAX_NITRO);
    if (e.type === "pizza") { s.score += 250; s.fuel = clamp(s.fuel + 6, 0, MAX_FUEL); }
    events.push({ type: "pickup", pickup: e.type, x: e.x, z: e.z, text });
  }

  function endRun(s, reason, events) {
    s.phase = "over";
    s.reason = reason;
    s.speed = 0;
    s.boostActive = false;
    events.push({ type: "over", reason, text: reason === "fuel" ? "Пицца остыла. Заезд окончен." : "Коробка помялась. И ты тоже." });
  }

  function update(s, input, dt) {
    const events = [];
    if (s.phase !== "running") return events;
    dt = clamp(Number.isFinite(dt) ? dt : 0, 0, 0.1);
    if (dt <= 0) return events;
    input = input || {};

    s.time += dt;
    s.shotCooldown = Math.max(0, s.shotCooldown - dt);
    s.invulnerable = Math.max(0, s.invulnerable - dt);
    s.boostLock = Math.max(0, s.boostLock - dt);
    s.comboTimer = Math.max(0, s.comboTimer - dt);
    if (s.comboTimer === 0) s.combo = 1;

    const wantBoost = !!input.boost && s.nitro > 0 && s.boostLock === 0;
    if (wantBoost && !s.boostActive) events.push({ type: "boost", text: "Нитро включено. Тормоза в отпуске." });
    s.boostActive = wantBoost;
    if (s.boostActive) {
      s.nitro = Math.max(0, s.nitro - 30 * dt);
      if (s.nitro === 0) { s.boostActive = false; s.boostLock = 0.6; }
    } else {
      s.nitro = Math.min(MAX_NITRO, s.nitro + 6 * dt);
    }

    const baseSpeed = 105 + Math.min(75, s.distance / 35);
    const targetSpeed = baseSpeed * (s.boostActive ? 1.55 : 1);
    s.speed += (targetSpeed - s.speed) * Math.min(1, dt * 4);
    const meters = (s.speed / 3.6) * dt;
    s.distance += meters;
    s.score += meters * 2;
    s.roadOffset = (s.roadOffset + dt * s.speed / 110) % 1;
    s.fuel = Math.max(0, s.fuel - (2.0 + (s.boostActive ? 1.2 : 0)) * dt);

    const newLevel = Math.floor(s.distance / DISTRICT_LENGTH) + 1;
    if (newLevel !== s.level) {
      s.level = newLevel;
      events.push({ type: "level", level: newLevel, text: "Район " + newLevel + ": " + district(s).name });
    }

    s.player.x += (s.player.lane - s.player.x) * Math.min(1, dt * 13);
    if (Math.abs(s.player.x - s.player.lane) < 0.001) s.player.x = s.player.lane;

    if (input.fire) fire(s);

    s.spawnTimer -= dt;
    if (s.spawnTimer <= 0) {
      spawnRow(s);
      s.spawnTimer = Math.max(0.68, 1.25 - s.distance / 5000);
    }

    const scroll = dt * (0.22 + s.speed / 600);
    for (const e of s.entities) {
      e.z += scroll * (e.kind === "pickup" ? 0.92 : 1);
      e.wobble += dt * 3;
    }

    for (const p of s.projectiles) {
      p.z -= dt * 1.55;
      p.spin += dt * 14;
    }

    //Projectile hits.
    for (const p of s.projectiles) {
      if (p.dead) continue;
      for (const e of s.entities) {
        if (e.hit || e.kind !== "obstacle") continue;
        if (Math.abs(e.x - p.x) < 0.48 && Math.abs(e.z - p.z) < 0.075) {
          p.dead = true;
          e.hp--;
          if (e.hp <= 0) destroyObstacle(s, e, events, false);
          else events.push({ type: "hit", x: e.x, z: e.z });
          break;
        }
      }
    }

    //Player collisions and pickups.
    for (const e of s.entities) {
      if (e.hit) continue;
      const nearZ = Math.abs(e.z - PLAYER_Z) < 0.065;
      const nearX = Math.abs(e.x - s.player.x) < 0.55;
      if (nearZ && nearX) {
        if (e.kind === "pickup") { collect(s, e, events); continue; }
        if (s.boostActive) { destroyObstacle(s, e, events, true); continue; }
        if (s.invulnerable > 0) continue;
        e.hit = true;
        s.health = Math.max(0, s.health - OBSTACLES[e.type].damage);
        s.invulnerable = 1.15;
        s.combo = 1;
        s.comboTimer = 0;
        s.speed *= 0.65;
        events.push({ type: "crash", x: e.x, z: e.z, damage: OBSTACLES[e.type].damage, text: CRASH_QUOTES[Math.floor(s.rng() * CRASH_QUOTES.length)] });
      }
      if (!e.passed && e.z > PLAYER_Z + 0.09) {
        e.passed = true;
        if (e.kind === "obstacle" && !e.hit) {
          s.dodged++;
          const dx = Math.abs(e.x - s.player.x);
          if (dx > 0.55 && dx < 1.35) {
            const points = 25 * s.combo;
            s.score += points;
            events.push({ type: "near", x: e.x, z: e.z, points, text: "На волосок! +" + points });
          }
        }
      }
    }

    s.entities = s.entities.filter((e) => !e.hit && e.z < 1.2);
    s.projectiles = s.projectiles.filter((p) => !p.dead && p.z > -0.12);

    if (s.health <= 0) endRun(s, "wreck", events);
    else if (s.fuel <= 0) endRun(s, "fuel", events);
    else if (s.fuel < 20 && s.fuel + 3 * dt >= 20) events.push({ type: "lowfuel", text: "Лампочка горит. Как и твоя надежда." });

    return events;
  }

  function snapshot(s) {
    return {
      phase: s.phase, time: s.time, distance: Math.floor(s.distance), score: Math.floor(s.score),
      speed: Math.round(s.speed), health: Math.ceil(s.health), fuel: Math.ceil(s.fuel),
      nitro: Math.ceil(s.nitro), combo: s.combo, kills: s.kills, dodged: s.dodged,
      shots: s.shots, level: s.level, district: district(s).name, reason: s.reason
    };
  }

  return {
    LANES, PLAYER_Z, MAX_HEALTH, MAX_FUEL, MAX_NITRO, SHOT_COOLDOWN, DISTRICT_LENGTH,
    DISTRICTS, OBSTACLES, PICKUPS, KILL_QUOTES, CRASH_QUOTES,
    clamp, makeRng, createState, startRun, steer, pause, resume, district,
    addEntity, spawnRow, fire, update, snapshot
  };
});
