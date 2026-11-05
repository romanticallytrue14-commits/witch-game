const $ = id => document.getElementById(id);

const recipes = [
  {
    name: "Ночное зрение",
    guest: "Слепой охотник",
    items: ["гриб", "ягода"],
    reward: 12
  },
  {
    name: "Лесное исцеление",
    guest: "Раненая странница",
    items: ["трава", "гриб"],
    reward: 10
  },
  {
    name: "Шёпот теней",
    guest: "Незнакомец в плаще",
    items: ["ягода", "трава"],
    reward: 15
  },
  {
    name: "Сон без сновидений",
    guest: "Уставшая ведьма",
    items: ["гриб", "гриб"],
    reward: 11
  }
];

let game = {
  coins: 0,
  served: 0,
  stock: { гриб: 2, трава: 2, ягода: 2 }
};

let pot = [];

try {
  const saved = JSON.parse(localStorage.getItem("witchGame2"));
  if (saved) game = saved;
} catch (e) {}

function save() {
  try {
    localStorage.setItem("witchGame2", JSON.stringify(game));
  } catch (e) {}
}

function currentRecipe() {
  return recipes[game.served % recipes.length];
}

function updateUI() {
  const r = currentRecipe();
  $("stats").textContent =
    "🪙 Монеты: " + game.coins + "   •   Заказов: " + game.served;

  $("order").textContent =
    r.guest + " просит: «" + r.name + "»";

  $("recipe").textContent =
    "Рецепт: " + r.items.join(" + ") +
    " • Награда: " + r.reward + " монет";

  $("stock").textContent =
    "🍄 " + game.stock["гриб"] +
    "   🌿 " + game.stock["трава"] +
    "   🫐 " + game.stock["ягода"];
}

$("gather").addEventListener("click", () => {
  const finds = ["гриб", "трава", "ягода"];
  const item = finds[Math.floor(Math.random() * finds.length)];

  game.stock[item]++;
  save();
  updateUI();

  const messages = {
    гриб: "Под старым пнём ты нашла гриб.",
    трава: "На тёмной поляне растёт целебная трава.",
    ягода: "В колючих ветвях прячутся ягоды."
  };

  $("forest-message").textContent = messages[item];
});

document.querySelectorAll("[data-item]").forEach(button => {
  button.addEventListener("click", () => {
    const item = button.dataset.item;

    if (pot.length >= 2) {
      $("status").textContent = "В котле уже два ингредиента.";
      return;
    }

    if (!game.stock[item]) {
      $("status").textContent =
        "Ингредиент закончился. Отправляйся в лес!";
      return;
    }

    game.stock[item]--;
    pot.push(item);
    save();
    updateUI();
    $("status").textContent = "В котле: " + pot.join(" + ");
  });
});

$("clear").addEventListener("click", () => {
  for (const item of pot) game.stock[item]++;
  pot = [];
  save();
  updateUI();
  $("status").textContent = "Ингредиенты возвращены в запас.";
});

$("brew").addEventListener("click", () => {
  if (pot.length !== 2) {
    $("status").textContent = "Добавь два ингредиента.";
    return;
  }

  const r = currentRecipe();
  const wanted = [...r.items].sort().join(",");
  const mixed = [...pot].sort().join(",");

  if (wanted === mixed) {
    game.coins += r.reward;
    game.served++;
    $("status").textContent =
      "Зелье готово! Гость заплатил " + r.reward + " монет.";
  } else {
    $("status").textContent =
      "Из котла вырвался чёрный дым. Это не тот рецепт!";
  }

  pot = [];
  save();
  updateUI();
});

updateUI();
// Картинка и звук

const canvas = $("scene");
const ctx = canvas.getContext("2d");

function drawScene(time) {
  const t = time / 1000;

  // Ночное небо
  const sky = ctx.createLinearGradient(0, 0, 0, 340);
  sky.addColorStop(0, "#101b1a");
  sky.addColorStop(1, "#34362a");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, 600, 340);

  // Луна
  ctx.fillStyle = "#c5c9a8";
  ctx.shadowColor = "#ced6b0";
  ctx.shadowBlur = 25;
  ctx.beginPath();
  ctx.arc(490, 62, 25, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;

  // Деревья
  for (let i = 0; i < 12; i++) {
    const x = i * 56 - 10;
    const height = 100 + (i * 37 % 75);
    ctx.fillStyle = i % 2 ? "#101d18" : "#15231c";
    ctx.beginPath();
    ctx.moveTo(x, 245);
    ctx.lineTo(x + 28, 245 - height);
    ctx.lineTo(x + 56, 245);
    ctx.fill();
  }

  // Земля
  ctx.fillStyle = "#161b13";
  ctx.fillRect(0, 240, 600, 100);

  // Избушка
  ctx.fillStyle = "#30271e";
  ctx.fillRect(90, 139, 185, 135);
  ctx.fillStyle = "#1b1915";
  ctx.beginPath();
  ctx.moveTo(70, 145);
  ctx.lineTo(181, 67);
  ctx.lineTo(295, 145);
  ctx.closePath();
  ctx.fill();

  // Окно и дверь
  ctx.shadowColor = "#e2a64f";
  ctx.shadowBlur = 20 + Math.sin(t * 3) * 4;
  ctx.fillStyle = "#e1aa56";
  ctx.fillRect(116, 163, 48, 48);
  ctx.shadowBlur = 0;
  ctx.strokeStyle = "#3a281d";
  ctx.lineWidth = 5;
  ctx.strokeRect(116, 163, 48, 48);
  ctx.beginPath();
  ctx.moveTo(140, 163);
  ctx.lineTo(140, 211);
  ctx.moveTo(116, 187);
  ctx.lineTo(164, 187);
  ctx.stroke();

  ctx.fillStyle = "#171410";
  ctx.fillRect(204, 175, 47, 99);
  ctx.fillStyle = "#ba9c63";
  ctx.beginPath();
  ctx.arc(240, 226, 3, 0, Math.PI * 2);
  ctx.fill();

  // Дым
  for (let i = 0; i < 5; i++) {
    const y = 210 - ((t * 18 + i * 20) % 100);
    const x = 407 + Math.sin(t + i) * 12;
    ctx.fillStyle = "rgba(165,190,161,0.13)";
    ctx.beginPath();
    ctx.arc(x, y, 12 + i * 2, 0, Math.PI * 2);
    ctx.fill();
  }

  // Огонь
  ctx.fillStyle = "#c0642c";
  ctx.shadowColor = "#e8903e";
  ctx.shadowBlur = 18;
  ctx.beginPath();
  ctx.moveTo(374, 302);
  ctx.lineTo(387, 270 - Math.sin(t * 8) * 5);
  ctx.lineTo(399, 298);
  ctx.lineTo(414, 265 - Math.sin(t * 7) * 5);
  ctx.lineTo(433, 302);
  ctx.fill();
  ctx.shadowBlur = 0;

  // Котёл
  ctx.fillStyle = "#111412";
  ctx.beginPath();
  ctx.ellipse(405, 259, 54, 38, 0, 0, Math.PI);
  ctx.fill();
  ctx.fillRect(351, 248, 108, 14);
  ctx.fillStyle = "#597b58";
  ctx.beginPath();
  ctx.ellipse(405, 248, 50, 9, 0, 0, Math.PI * 2);
  ctx.fill();

  // Пузырьки и светлячки
  for (let i = 0; i < 4; i++) {
    ctx.fillStyle = "#a6c98b";
    ctx.beginPath();
    ctx.arc(375 + i * 18, 248 - Math.abs(Math.sin(t * 2 + i)) * 8,
      2 + i % 2, 0, Math.PI * 2);
    ctx.fill();
  }

  for (let i = 0; i < 9; i++) {
    ctx.fillStyle = "rgba(194,215,145,0.7)";
    ctx.beginPath();
    ctx.arc(300 + i * 31 + Math.sin(t + i) * 9,
      90 + (i * 41 % 140) + Math.cos(t + i) * 8,
      2, 0, Math.PI * 2);
    ctx.fill();
  }

  requestAnimationFrame(drawScene);
}

requestAnimationFrame(drawScene);

// Тихий фоновый звук
let audio = null;
let volume = null;
let soundOn = false;

$("sound").addEventListener("click", async () => {
  try {
    if (!audio) {
      const AudioEngine = window.AudioContext || window.webkitAudioContext;
      audio = new AudioEngine();
      volume = audio.createGain();
      volume.gain.value = 0;
      volume.connect(audio.destination);

      const drone = audio.createOscillator();
      drone.type = "sine";
      drone.frequency.value = 65;
      drone.connect(volume);
      drone.start();
    }

    if (audio.state === "suspended") await audio.resume();
    soundOn = !soundOn;
    volume.gain.setTargetAtTime(
      soundOn ? 0.035 : 0, audio.currentTime, 0.3
    );
    $("sound").textContent = soundOn
      ? "🔊 Выключить звук"
      : "🔇 Включить звук";
  } catch (e) {
    $("sound").textContent = "Не удалось включить звук";
  }
});
