"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "pizza-apocalypse");
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");

test("manifest is installable and scoped to the game directory", () => {
  const manifest = JSON.parse(read("manifest.webmanifest"));
  assert.equal(manifest.name, "Пицца апокалипсиса");
  assert.equal(manifest.short_name, "Пицца 666");
  assert.equal(manifest.lang, "ru");
  assert.equal(manifest.display, "standalone");
  assert.equal(manifest.start_url, "./");
  assert.equal(manifest.scope, "./");
  assert.ok(manifest.icons.some((i) => i.sizes === "192x192" && i.purpose === "any"));
  assert.ok(manifest.icons.some((i) => i.sizes === "512x512" && i.purpose === "any"));
  assert.ok(manifest.icons.some((i) => i.sizes === "512x512" && i.purpose === "maskable"));
});

test("PNG icons exist with the declared dimensions", () => {
  const manifest = JSON.parse(read("manifest.webmanifest"));
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
});

test("HTML links manifest, iOS icon and install UI", () => {
  const html = read("index.html");
  assert.match(html, /rel="manifest" href="manifest\.webmanifest"/);
  assert.match(html, /rel="apple-touch-icon"[^>]*href="apple-touch-icon\.png"/);
  assert.match(html, /name="apple-mobile-web-app-capable" content="yes"/);
  assert.match(html, /id="install"/);
  assert.match(html, /id="install-dialog"/);
  assert.match(html, /<script src="install\.js"><\/script>/);
});

test("service worker precaches every runtime asset and keeps its own cache prefix", () => {
  const sw = read("sw.js");
  assert.match(sw, /const CACHE_PREFIX = "pizza-apocalypse-"/);
  const assets = [...sw.matchAll(/"\.\/([^"]+)"/g)].map((m) => m[1]);
  assert.ok(assets.includes("index.html"));
  assert.ok(assets.includes("engine.js"));
  assert.ok(assets.includes("game.js"));
  assert.ok(assets.includes("install.js"));
  assert.ok(assets.includes("manifest.webmanifest"));
  assert.ok(assets.includes("assets/oswald-cyrillic.woff2"));
  for (const asset of assets) {
    if (asset === "") continue;
    assert.ok(fs.existsSync(path.join(root, asset)), "missing precache asset: " + asset);
  }
  assert.match(sw, /request\.mode === "navigate"/);
  assert.match(sw, /caches\.match\(indexURL\)/);
});

test("install helper registers a relative service worker and handles native prompt", () => {
  const install = read("install.js");
  assert.match(install, /navigator\.serviceWorker\.register\("sw\.js", \{ scope: "\.\/" \}\)/);
  assert.match(install, /beforeinstallprompt/);
  assert.match(install, /appinstalled/);
  assert.match(install, /prompt\.prompt\(\)/);
  assert.match(install, /window\.location\.href/);
});
