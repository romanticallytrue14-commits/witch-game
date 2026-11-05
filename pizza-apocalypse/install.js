/* Установка на главный экран и офлайн-готовность. Не зависит от игрового цикла. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dialog = $("install-dialog");
  const openButton = $("install");
  const nativeButton = $("install-native");
  const status = $("install-status");
  const linkInput = $("install-link");
  const copyButton = $("install-copy");
  let installPrompt = null;
  let installed = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

  function isWebContext() {
    return /^https?:$/.test(window.location.protocol);
  }

  function setStatus(text, kind) {
    status.textContent = text;
    status.dataset.state = kind || "pending";
  }

  function refreshInstallButton() {
    nativeButton.hidden = !installPrompt || installed;
    openButton.classList.toggle("installed", installed);
    if (installed) {
      openButton.title = "Игра уже установлена";
      openButton.setAttribute("aria-label", "Игра установлена. Информация об установке");
    }
  }

  function openDialog() {
    //Never leave a live run running behind the installation sheet.
    if (window.PizzaGame && window.PizzaGame.snapshot().phase === "running") $("pause").click();
    linkInput.value = isWebContext() ? window.location.href.split("#")[0] : "";
    $("install-link-row").hidden = !isWebContext();
    if (installed) setStatus("Игра уже установлена. Ярлык ищи на главном экране телефона.", "ready");
    else if (installPrompt) setStatus("Браузер готов установить игру. Нажми кнопку ниже.", "ready");
    else if (!isWebContext()) setStatus("Для установки открой игру по HTTPS-ссылке, а не как локальный файл.", "pending");
    else if (!window.isSecureContext) setStatus("Для установки нужна защищённая HTTPS-ссылка.", "pending");
    else if (status.dataset.state !== "ready") setStatus("Офлайн-копия ещё загружается. Подожди немного или обнови страницу.", "pending");

    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    $("install-close").focus();
  }

  function closeDialog() {
    if (typeof dialog.close === "function" && dialog.open) dialog.close();
    else dialog.removeAttribute("open");
    openButton.focus();
  }

  openButton.addEventListener("click", openDialog);
  $("install-close").addEventListener("click", closeDialog);
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
  });
  dialog.addEventListener("cancel", () => { openButton.focus(); });

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    refreshInstallButton();
    if (dialog.open && !installed) setStatus("Браузер готов установить игру. Нажми кнопку ниже.", "ready");
  });

  nativeButton.addEventListener("click", async () => {
    if (!installPrompt) return;
    const prompt = installPrompt;
    installPrompt = null;
    refreshInstallButton();
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (choice && choice.outcome === "accepted") {
        installed = true;
        setStatus("Готово! Игра появится на главном экране телефона.", "ready");
      } else {
        setStatus("Установка отменена. Можно добавить ярлык через меню браузера вручную.", "pending");
      }
    } catch {
      setStatus("Браузер не показал окно установки. Используй инструкцию ниже.", "pending");
    }
    refreshInstallButton();
  });

  window.addEventListener("appinstalled", () => {
    installed = true;
    installPrompt = null;
    refreshInstallButton();
    setStatus("Игра установлена. Доставляй пиццу даже без интернета!", "ready");
  });

  copyButton.addEventListener("click", async () => {
    const value = linkInput.value;
    if (!value) return;
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(value);
      copyButton.textContent = "Скопировано!";
    } catch {
      linkInput.focus();
      linkInput.select();
      copyButton.textContent = "Выдели и скопируй";
    }
    setTimeout(() => { copyButton.textContent = "Скопировать ссылку"; }, 2200);
  });

  function waitForActive(registration) {
    if (registration.active) return Promise.resolve(registration);
    return new Promise((resolve, reject) => {
      const worker = registration.installing || registration.waiting;
      if (!worker) { reject(new Error("No service worker")); return; }
      const timer = setTimeout(() => reject(new Error("Service worker timeout")), 30000);
      worker.addEventListener("statechange", () => {
        if (worker.state === "activated") { clearTimeout(timer); resolve(registration); }
        if (worker.state === "redundant") { clearTimeout(timer); reject(new Error("Service worker redundant")); }
      });
    });
  }

  async function registerOffline() {
    if (!isWebContext()) {
      setStatus("Локальный файл уже работает без сети. Установка на телефон доступна по HTTPS-ссылке.", "pending");
      return;
    }
    if (!window.isSecureContext || !("serviceWorker" in navigator)) {
      setStatus("Этот браузер не поддерживает офлайн-установку. Открой игру в Chrome или Safari по HTTPS-ссылке.", "pending");
      return;
    }
    try {
      const registration = await navigator.serviceWorker.register("sw.js", { scope: "./" });
      await waitForActive(registration);
      setStatus("Офлайн-копия готова. После установки можно играть без интернета.", "ready");
    } catch {
      setStatus("Не удалось загрузить офлайн-копию. Проверь сеть и обнови страницу.", "error");
    }
  }

  refreshInstallButton();
  registerOffline();
})();
