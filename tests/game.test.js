const fs = require("fs");
const { JSDOM } = require("jsdom");

const path = require("path");
const repo = path.resolve(__dirname, "..");
const html = fs.readFileSync(repo + "/index.html", "utf8");
const gameJs = fs.readFileSync(repo + "/game.js", "utf8");

const EMOJI = { гриб: "🍄", трава: "🌿", ягода: "🫐", цветок: "🌙" };
const ROLL = { гриб: 0.0, трава: 0.4, ягода: 0.9 };

const RECIPES = [
  { id: "dreamless", name: "Сон без сновидений", items: ["гриб", "гриб"] },
  { id: "spring", name: "Дыхание весны", items: ["трава", "трава"] },
  { id: "thorn", name: "Терновый сон", items: ["ягода", "ягода"] },
  { id: "eclipse", name: "Лунное затмение", items: ["цветок", "цветок"] },
  { id: "healing", name: "Лесное исцеление", items: ["трава", "гриб"] },
  { id: "night-eye", name: "Ночное зрение", items: ["гриб", "ягода"] },
  { id: "call", name: "Зов леса", items: ["цветок", "гриб"] },
  { id: "shadow", name: "Шёпот теней", items: ["ягода", "трава"] },
  { id: "moonpath", name: "Лунная дорожка", items: ["цветок", "трава"] },
  { id: "fog", name: "Туман забвения", items: ["цветок", "ягода"] }
];

let failures = 0;
function check(name, cond, extra = "") {
  if (cond) console.log("  ✓ " + name);
  else {
    failures++;
    console.log("  ✗ " + name + (extra ? "  →  " + extra : ""));
  }
}

function build(seedStorage = null, options = {}) {
  const pre = `<script>
    Math.random = () => 0;
    HTMLCanvasElement.prototype.getContext = function () {
      const grad = { addColorStop() {} };
      return new Proxy({}, {
        get: (target, prop) => {
          if (prop === "canvas") return this;
          return (...args) => (String(prop).indexOf("create") === 0 ? grad : undefined);
        }
      });
    };
    window.matchMedia = () => ({ matches: ${options.reduceMotion === false ? "false" : "true"}, addEventListener() {}, addListener() {} });
    window.requestAnimationFrame = (cb) => { window.__frame = cb; return 42; };
    window.cancelAnimationFrame = () => {};
    window.confirm = () => true;
    ${seedStorage ? `localStorage.setItem("witchGame3", ${JSON.stringify(seedStorage)});` : ""}
  </script>`;

  const page = html
    .replace('<link rel="stylesheet" href="style.css">', "")
    .replace('<script src="game.js"></script>', pre + "<script>" + gameJs + "</script>");

  const dom = new JSDOM(page, { runScripts: "dangerously", url: "https://witch.test/" });
  const win = dom.window;
  const $ = (id) => win.document.getElementById(id);
  const click = (el) => el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  const ing = (key) => win.document.querySelector('[data-item="' + key + '"]');
  const amount = (key) => Number(
    Array.from(win.document.querySelectorAll(".chip"))
      .find((c) => c.textContent.includes(EMOJI[key]))
      .textContent.replace(/\D/g, "")
  );
  const orderName = () => {
    const match = $("order").textContent.match(/«(.+)»/);
    return match ? match[1] : "";
  };
  const recipe = () => RECIPES.find((r) => r.name === orderName());
  const press = (key) => win.document.body.dispatchEvent(
    new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })
  );
  return { dom, win, $, click, ing, amount, orderName, recipe, press };
}

function ensureStock(app, key, need) {
  let guard = 0;
  while (app.amount(key) < need && guard++ < 500) {
    if (key === "цветок") {
      app.win.Math.random = () => 0.5;
      app.click(app.$("gather-deep"));
    } else {
      app.win.Math.random = () => ROLL[key];
      app.click(app.$("gather-edge"));
    }
  }
  app.win.Math.random = () => 0;
  return app.amount(key) >= need;
}

function brewRecipe(app, recipe, steerTo = null) {
  const need = {};
  for (const item of recipe.items) need[item] = (need[item] || 0) + 1;
  for (const key of Object.keys(need)) {
    if (!ensureStock(app, key, need[key])) return false;
  }

  const currentId = recipe.id;
  if (steerTo) {
    const pool = RECIPES.filter((r) => r.id !== currentId);
    const index = pool.findIndex((r) => r.id === steerTo.id);
    app.win.Math.random = () => (index + 0.5) / pool.length;
  } else {
    app.win.Math.random = () => 0;
  }

  for (const item of recipe.items) app.click(app.ing(item));
  app.click(app.$("brew"));
  return true;
}

/* ---------------------------------------------------------- */

console.log("\n1. Загрузка и стартовое состояние");
let app = build();
check("версия в шапке", app.$("version").textContent === "v2.0", app.$("version").textContent);
check("сцена рисуется без ошибок", true);
check("первый гость — «Сон без сновидений»", app.orderName() === "Сон без сновидений", app.orderName());
check("подсказка описывает запах, а не ингредиенты",
  app.$("guest-hint").textContent.includes("Пахнет") && !app.$("guest-hint").textContent.includes("Гриб"),
  app.$("guest-hint").textContent);
check("базовый запас 2/2/2/0", ["гриб", "трава", "ягода", "цветок"].map((k) => app.amount(k)).join("/") === "2/2/2/0",
  ["гриб", "трава", "ягода", "цветок"].map((k) => app.amount(k)).join("/"));
check("попытки 2 / 2", app.$("attempts").textContent.includes("2 / 2"), app.$("attempts").textContent);
check("гримуар: 10 скрытых рецептов", app.win.document.querySelectorAll(".grim").length === 10 &&
  app.win.document.querySelectorAll(".grim.unknown").length === 10);
check("счётчик гримуара 0 / 10", app.$("grimoire-count").textContent === "0 / 10", app.$("grimoire-count").textContent);
check("три улучшения в лавке", app.win.document.querySelectorAll(".upgrade").length === 3);
check("шесть достижений, все закрыты", app.win.document.querySelectorAll(".ach").length === 6 &&
  app.win.document.querySelectorAll(".ach.unlocked").length === 0);
check("счётчик достижений 0 / 6", app.$("ach-count").textContent === "0 / 6", app.$("ach-count").textContent);
check("нет уведомлений при старте", app.win.document.querySelectorAll(".toast").length === 0);
check("подсказка стоит 4 монеты", app.$("hint").textContent.includes("4 🪙"), app.$("hint").textContent);
check("клавиши подписаны у ингредиентов", app.win.document.querySelectorAll(".ing-key").length === 4);

console.log("\n2. Лес");
app.win.Math.random = () => 0;
app.click(app.$("gather-edge"));
check("опушка пополняет запас", app.amount("гриб") === 3, String(app.amount("гриб")));
check("сообщение леса обновилось", app.$("forest-message").textContent.includes("опушке"), app.$("forest-message").textContent);
app.win.Math.random = () => 0.5;
app.click(app.$("gather-deep"));
check("чаща приносит лунный цветок", app.amount("цветок") === 1, String(app.amount("цветок")));
app.win.Math.random = () => 0;
app.click(app.$("gather-deep"));
check("в чаще можно уйти ни с чем", app.$("forest-message").textContent.includes("пустыми руками"),
  app.$("forest-message").textContent);

console.log("\n3. Подсказка без денег");
app.click(app.$("hint"));
check("подсказка не выдаётся без монет", app.$("status").textContent.includes("Не хватает монет"), app.$("status").textContent);
check("монеты не ушли в минус", app.$("stat-coins").textContent === "0");

console.log("\n4. Провалы, попытки и достижение «Спокойствие»");
brewRecipe(app, { id: "dreamless", items: ["гриб", "трава"] });
check("неверный рецепт → чёрный дым", app.$("status").textContent.includes("чёрный дым"), app.$("status").textContent);
check("осталась одна попытка", app.$("attempts").textContent.includes("1 / 2"), app.$("attempts").textContent);
check("подсказка о нехватке ингредиента", app.$("supply").textContent.includes("Гриб") || app.$("supply").textContent === "",
  app.$("supply").textContent);

brewRecipe(app, { id: "dreamless", items: ["гриб", "трава"] });
check("вторая ошибка → гость ушёл", app.$("status").textContent.includes("ушёл"), app.$("status").textContent);
check("счётчик ушедших = 1", app.$("stat-left").textContent === "1", app.$("stat-left").textContent);
check("достижение «Спокойствие» открыто", app.win.document.querySelector("#achievements").textContent.includes("Спокойствие") &&
  app.$("ach-count").textContent === "1 / 6", app.$("ach-count").textContent);
check("уведомление о достижении показано", app.win.document.querySelectorAll(".toast").length === 1,
  String(app.win.document.querySelectorAll(".toast").length));

console.log("\n5. Успешная варка");
check("новый гость — «Дыхание весны»", app.orderName() === "Дыхание весны", app.orderName());
brewRecipe(app, app.recipe());
check("зелье готово", app.$("status").textContent.includes("Зелье готово"), app.$("status").textContent);
check("начислено 13 монет", app.$("stat-coins").textContent === "13", app.$("stat-coins").textContent);
check("заказ засчитан", app.$("stat-served").textContent === "1", app.$("stat-served").textContent);
check("серия = 1", app.$("stat-streak").textContent === "1", app.$("stat-streak").textContent);
check("рецепт открыт", app.win.document.querySelectorAll(".grim.known").length === 1);
check("счётчик гримуара 1 / 10", app.$("grimoire-count").textContent === "1 / 10", app.$("grimoire-count").textContent);
check("достижение «Первый гость» открыто", app.$("ach-count").textContent === "2 / 6", app.$("ach-count").textContent);

console.log("\n6. Подсказки за монеты");
check("новый гость — «Сон без сновидений»", app.orderName() === "Сон без сновидений", app.orderName());
app.click(app.$("hint"));
check("списано 4 монеты", app.$("stat-coins").textContent === "9", app.$("stat-coins").textContent);
check("ингредиент раскрыт", app.$("revealed").textContent.includes("Гримуар:"), app.$("revealed").textContent);
check("для одинаковых ингредиентов подсказка одна", app.$("hint").disabled === true);

console.log("\n7. Улучшения");
app.click(app.win.document.querySelectorAll(".upgrade")[0]);
check("без денег улучшение не покупается", app.$("status").textContent.includes("Нужно 20"), app.$("status").textContent);

console.log("\n8. Сохранение и перезагрузка");
const savedRaw = app.win.localStorage.getItem("witchGame3");
const saved = JSON.parse(savedRaw);
check("сохранены открытые рецепты", Array.isArray(saved.discovered) && saved.discovered.length === 1);
check("сохранены достижения", saved.achievements.length === 2, JSON.stringify(saved.achievements));
check("сохранён лучший стрик", saved.bestStreak === 1, String(saved.bestStreak));
check("сохранена версия сохранения", saved.version === 2, String(saved.version));

let app2 = build(savedRaw);
check("монеты восстановлены", app2.$("stat-coins").textContent === String(saved.coins), app2.$("stat-coins").textContent);
check("запас восстановлен", ["гриб", "трава", "ягода", "цветок"].every((k) => app2.amount(k) === saved.stock[k]),
  ["гриб", "трава", "ягода", "цветок"].map((k) => app2.amount(k)).join("/"));
check("достижения восстановлены", app2.$("ach-count").textContent === "2 / 6", app2.$("ach-count").textContent);

console.log("\n9. Перезагрузка в середине варки (старый баг)");
ensureStock(app2, "гриб", 2);
app2.click(app2.ing("гриб"));
const midSaved = JSON.parse(app2.win.localStorage.getItem("witchGame3"));
check("ингредиент в котле сохранён", midSaved.pot.length === 1, JSON.stringify(midSaved.pot));

let app3 = build(JSON.stringify(midSaved));
check("после перезагрузки ингредиент в котле", JSON.parse(app3.win.localStorage.getItem("witchGame3")).pot.length === 1);
check("запас соответствует сохранению", app3.amount("гриб") === midSaved.stock["гриб"],
  app3.amount("гриб") + " vs " + midSaved.stock["гриб"]);
app3.click(app3.ing("гриб"));
check("можно добрать второй ингредиент", app3.$("status").textContent.includes("В котле"), app3.$("status").textContent);
app3.click(app3.$("brew"));
check("варка после перезагрузки работает", app3.$("status").textContent.includes("Зелье готово"), app3.$("status").textContent);

console.log("\n10. Клавиатура");
let appK = build();
appK.press("1");
check("цифра кладёт ингредиент в котёл", appK.$("status").textContent.includes("В котле"), appK.$("status").textContent);
appK.press("1");
appK.press("Enter");
check("Enter варит зелье", appK.$("status").textContent.includes("Зелье готово"), appK.$("status").textContent);
ensureStock(appK, "гриб", 1);
appK.press("1");
check("ингредиент снова в котле", appK.$("status").textContent.includes("В котле"), appK.$("status").textContent);
const stockBeforeEsc = appK.amount("гриб");
appK.press("Escape");
check("Esc очищает котёл", appK.$("status").textContent.includes("возвращены"), appK.$("status").textContent);
check("Esc возвращает ингредиент", appK.amount("гриб") === stockBeforeEsc + 1,
  appK.amount("гриб") + " vs " + (stockBeforeEsc + 1));

console.log("\n11. Битые данные и рестарт");
let app4 = build(JSON.stringify({
  version: 1, coins: "abc", served: -5, leftGuests: null, streak: "7.5", bestStreak: 12,
  stock: { гриб: "x" }, gathered: { цветок: "много" }, pot: ["камень"],
  discovered: ["nope"], achievements: ["first-brew", "fake"], upgrades: ["bed", "unknown"],
  order: { recipeId: "нет", tries: 99 }
}));
check("битые числа обнулены",
  app4.$("stat-coins").textContent === "0" && app4.$("stat-served").textContent === "0",
  app4.$("stat-coins").textContent + "/" + app4.$("stat-served").textContent);
check("невалидный заказ заменён", app4.$("order").textContent.length > 5);
check("невалидные улучшения и достижения отброшены",
  app4.win.document.querySelectorAll(".upgrade.owned").length === 1 &&
  app4.$("ach-count").textContent === "1 / 6",
  app4.win.document.querySelectorAll(".upgrade.owned").length + "/" + app4.$("ach-count").textContent);
check("лучший стрик сохранился как число", JSON.parse(app4.win.localStorage.getItem("witchGame3")).bestStreak === 12);
app4.click(app4.$("reset"));
check("рестарт обнуляет статистику и достижения",
  app4.$("stat-coins").textContent === "0" && app4.$("ach-count").textContent === "0 / 6",
  app4.$("ach-count").textContent);
check("рестарт очищает котёл", JSON.parse(app4.win.localStorage.getItem("witchGame3")).pot.length === 0);
check("рестарт возвращает базовый запас", ["гриб", "трава", "ягода", "цветок"].map((k) => app4.amount(k)).join("/") === "2/2/2/0");

console.log("\n12. Полный цикл: все 10 рецептов");
let app5 = build();
const seen = [];
let guard = 0;
let allOk = true;

while (seen.length < RECIPES.length && guard++ < 40) {
  const recipe = app5.recipe();
  if (!recipe) { allOk = false; console.log("    ! неизвестный заказ: " + app5.orderName()); break; }

  const upcoming = RECIPES.find((r) => !seen.includes(r.id) && r.id !== recipe.id);
  if (!brewRecipe(app5, recipe, upcoming || null)) {
    allOk = false;
    console.log("    ! не удалось добыть ингредиенты для " + recipe.name);
    break;
  }

  if (!app5.$("status").textContent.includes("Зелье готово")) {
    allOk = false;
    console.log("    ! " + recipe.name + ": " + app5.$("status").textContent);
    break;
  }
  seen.push(recipe.id);
}

check("все 10 рецептов сварились", allOk && seen.length === 10, seen.length + " шт.");
check("гримуар открыт полностью", app5.$("grimoire-count").textContent === "10 / 10", app5.$("grimoire-count").textContent);
check("засчитано 10 заказов", app5.$("stat-served").textContent === "10", app5.$("stat-served").textContent);
check("серия = 10", app5.$("stat-streak").textContent === "10", app5.$("stat-streak").textContent);
check("монеты с бонусами за серию", Number(app5.$("stat-coins").textContent) === 207, app5.$("stat-coins").textContent);
check("открыто пять достижений", app5.$("ach-count").textContent === "5 / 6", app5.$("ach-count").textContent);
check("«Полный гримуар» открыт", app5.win.document.querySelector("#achievements").textContent.includes("Полный гримуар"));

console.log("\n13. Долгая свеча и бесплатные подсказки");
const orb = Array.from(app5.win.document.querySelectorAll(".upgrade")).find((b) => b.textContent.includes("Хрустальный шар"));
app5.click(orb);
check("хрустальный шар куплен", app5.win.document.querySelectorAll(".upgrade.owned").length === 1,
  String(app5.win.document.querySelectorAll(".upgrade.owned").length));
check("подсказки бесплатны", app5.$("hint").textContent.includes("бесплатно"), app5.$("hint").textContent);

const candle = Array.from(app5.win.document.querySelectorAll(".upgrade")).find((b) => b.textContent.includes("Долгая свеча"));
app5.click(candle);
check("долгая свеча куплена", app5.win.document.querySelectorAll(".upgrade.owned").length === 2,
  String(app5.win.document.querySelectorAll(".upgrade.owned").length));
check("у текущего гостя три попытки", app5.$("attempts").textContent.includes("3 / 3"), app5.$("attempts").textContent);
const coinsAfter = Number(app5.$("stat-coins").textContent);
check("покупки списали 65 монет", coinsAfter === 207 - 65, String(coinsAfter));

let app6 = build(app5.win.localStorage.getItem("witchGame3"));
check("три попытки сохраняются после перезагрузки", app6.$("attempts").textContent.includes("3 / 3"),
  app6.$("attempts").textContent);
check("купленные улучшения на месте после перезагрузки",
  app6.win.document.querySelectorAll(".upgrade.owned").length === 2,
  String(app6.win.document.querySelectorAll(".upgrade.owned").length));

console.log("\n14. Грядка");
const bed = Array.from(app5.win.document.querySelectorAll(".upgrade")).find((b) => b.textContent.includes("грядка"));
app5.click(bed);
check("грядка куплена", app5.win.document.querySelectorAll(".upgrade.owned").length === 3,
  String(app5.win.document.querySelectorAll(".upgrade.owned").length));
const before = app5.amount("гриб") + app5.amount("трава") + app5.amount("ягода") + app5.amount("цветок");
app5.win.Math.random = () => 0;
app5.click(app5.$("gather-edge"));
const after = app5.amount("гриб") + app5.amount("трава") + app5.amount("ягода") + app5.amount("цветок");
check("грядка приносит два ингредиента", after - before === 2, "разница " + (after - before));

console.log("\n15. PWA-файлы");
const manifest = JSON.parse(fs.readFileSync(repo + "/manifest.webmanifest", "utf8"));
check("манифест описывает игру", manifest.name === "Ведьмина лавка" && manifest.display === "standalone");
check("иконки 192 и 512 на месте",
  fs.existsSync(repo + "/icon-192.png") && fs.existsSync(repo + "/icon-512.png"));
check("манифест ссылается на обе PNG-иконки",
  manifest.icons.some((i) => i.sizes === "192x192") && manifest.icons.some((i) => i.sizes === "512x512"));
check("манифест подключён в разметке", html.includes('rel="manifest" href="manifest.webmanifest"'));
check("service worker зарегистрирован в коде", gameJs.includes('navigator.serviceWorker.register("sw.js")'));
check("sw.js кэширует оболочку", fs.readFileSync(repo + "/sw.js", "utf8").includes("./game.js"));

console.log("\n16. Анимированный кадр (reduce-motion выключен)");
let appM = build(null, { reduceMotion: false });
const step = (t) => {
  const frame = appM.win.__frame;
  appM.win.__frame = null;
  if (typeof frame === "function") frame(t);
};
check("анимация запущена", typeof appM.win.__frame === "function");
step(appM.win.performance.now());
check("кадр отрисован, следующий запланирован", typeof appM.win.__frame === "function");

ensureStock(appM, "гриб", 2);
appM.win.Math.random = () => 0;
appM.click(appM.ing("гриб"));
appM.click(appM.ing("гриб"));
appM.click(appM.$("brew"));
check("варка при включённой анимации", appM.$("status").textContent.includes("Зелье готово"), appM.$("status").textContent);

const base = appM.win.performance.now();
step(base + 40);
step(base + 400);
step(base + 950);
check("вспышка удачи отрисована без ошибок", true);

ensureStock(appM, "гриб", 2);
ensureStock(appM, "трава", 1);
appM.click(appM.ing("гриб"));
appM.click(appM.ing("трава"));
appM.click(appM.$("brew"));
check("провал при включённой анимации", appM.$("status").textContent.includes("чёрный дым"), appM.$("status").textContent);
step(appM.win.performance.now() + 30);
step(appM.win.performance.now() + 800);
check("вспышка провала отрисована без ошибок", true);

console.log("\n" + (failures === 0 ? "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" : failures + " ПРОВЕРОК УПАЛО"));
process.exit(failures === 0 ? 0 : 1);
