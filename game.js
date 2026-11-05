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
