(function () {
  "use strict";

  const Engine = window.DrumMachineEngine;
  const $ = (selector, root = document) => root.querySelector(selector);
  const STORAGE_KEY = "witchbeat-pattern-v1";
  const STEPS_PER_PAGE = 8;
  const STORAGE = (() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return { state: raw ? Engine.sanitizeState(JSON.parse(raw)) : Engine.createPreset("midnight"), restored: Boolean(raw) };
    } catch (_) {
      return { state: Engine.createPreset("midnight"), restored: false };
    }
  })();

  let state = STORAGE.state;
  let page = 0;
  let currentStep = -1;
  let playing = false;
  let starting = false;
  let audioEngine = null;
  let schedulerId = null;
  let nextStep = 0;
  let nextStepTime = 0;
  let playbackToken = 0;
  let saveTimer = null;
  let toastTimer = null;
  let installPrompt = null;
  const tapTimes = [];

  const playButton = $("#playButton");
  const playLabel = $("#playLabel");
  const playState = $("#playState");
  const stepReadout = $("#stepReadout");
  const stepNumbers = $("#stepNumbers");
  const trackRows = $("#trackRows");
  const patternDescription = $("#patternDescription");
  const saveStatus = $("#saveStatus");
  const toast = $("#toast");

  function loadPresetButtons() {
    const list = $("#presetList");
    list.replaceChildren();
    for (const [id, preset] of Object.entries(Engine.PATTERNS)) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "preset-chip";
      button.dataset.preset = id;
      button.setAttribute("aria-pressed", String(state.preset === id));
      button.innerHTML = '<span class="preset-style"></span><span class="preset-name"></span>';
      button.querySelector(".preset-style").textContent = preset.style;
      button.querySelector(".preset-name").textContent = preset.name;
      list.appendChild(button);
    }
  }

  function updateSaveStatus(kind, text) {
    saveStatus.classList.remove("saving", "error");
    if (kind) saveStatus.classList.add(kind);
    saveStatus.lastChild.textContent = " " + text;
  }

  function saveNow() {
    if (saveTimer !== null) {
      window.clearTimeout(saveTimer);
      saveTimer = null;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      updateSaveStatus("", "СОХРАНЕНО");
      return true;
    } catch (_) {
      updateSaveStatus("error", "НЕ СОХРАНЕНО");
      return false;
    }
  }

  function queueSave() {
    updateSaveStatus("saving", "СОХРАНЯЮ");
    if (saveTimer !== null) window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(saveNow, 240);
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("is-visible");
    if (toastTimer !== null) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 1900);
  }

  function updateRangeFill(input) {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const value = Number(input.value);
    const percent = max === min ? 0 : ((value - min) / (max - min)) * 100;
    input.style.setProperty("--range-progress", percent + "%");
  }

  function updateControls() {
    const tempo = $("#tempoRange");
    const swing = $("#swingRange");
    const volume = $("#volumeRange");
    tempo.value = String(state.tempo);
    $("#tempoValue").textContent = String(state.tempo);
    swing.value = String(state.swing);
    $("#swingValue").textContent = state.swing + "%";
    volume.value = String(Math.round(state.volume * 100));
    $("#volumeValue").textContent = Math.round(state.volume * 100) + "%";
    for (const input of [tempo, swing, volume]) updateRangeFill(input);
    if (audioEngine) audioEngine.setVolume(state.volume);
  }

  function updatePatternDetails() {
    const preset = Engine.PATTERNS[state.preset];
    patternDescription.textContent = preset
      ? preset.description
      : "Твой собственный ритм. Все изменения сохраняются на этом устройстве.";
    for (const button of $("#presetList").querySelectorAll("[data-preset]")) {
      button.setAttribute("aria-pressed", String(button.dataset.preset === state.preset));
    }
  }

  function renderPageButtons() {
    for (const button of document.querySelectorAll(".page-switch [data-page]")) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.page) === page));
    }
  }

  function renderSequencer() {
    renderPageButtons();
    stepNumbers.replaceChildren();
    const pageStart = page * STEPS_PER_PAGE;
    for (let offset = 0; offset < STEPS_PER_PAGE; offset++) {
      const step = pageStart + offset;
      const number = document.createElement("span");
      number.className = "step-number" + (step % 4 === 0 ? " bar-start" : "");
      number.dataset.stepNumber = String(step);
      number.textContent = String(step + 1).padStart(2, "0");
      if (step === currentStep) number.classList.add("is-playhead");
      stepNumbers.appendChild(number);
    }

    trackRows.replaceChildren();
    for (const track of Engine.TRACKS) {
      const row = document.createElement("div");
      row.className = "track-row";
      row.style.setProperty("--track-color", track.color);

      const audition = document.createElement("button");
      audition.type = "button";
      audition.className = "track-pad";
      audition.dataset.audition = track.id;
      audition.setAttribute("aria-label", "Прослушать: " + track.name);
      audition.title = "Прослушать " + track.name;
      audition.innerHTML = '<span class="track-glyph" aria-hidden="true"></span><span class="track-copy"><strong></strong><small></small></span>';
      audition.querySelector(".track-glyph").textContent = track.key;
      audition.querySelector(".track-copy strong").textContent = track.short;
      audition.querySelector(".track-copy small").textContent = track.hint;
      row.appendChild(audition);

      const cells = document.createElement("div");
      cells.className = "step-cells";
      for (let offset = 0; offset < STEPS_PER_PAGE; offset++) {
        const step = pageStart + offset;
        const active = state.steps[track.id][step] === true;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "step-toggle" + (active ? " is-active" : "") + (step === currentStep ? " is-playhead" : "");
        button.dataset.track = track.id;
        button.dataset.step = String(step);
        button.setAttribute("aria-pressed", String(active));
        button.setAttribute("aria-label", "Шаг " + (step + 1) + ": " + track.name + ", " + (active ? "включён" : "выключен"));
        button.title = "Шаг " + (step + 1) + " · " + track.name;
        cells.appendChild(button);
      }
      row.appendChild(cells);
      trackRows.appendChild(row);
    }
  }

  function renderPlayhead() {
    stepReadout.firstChild.nodeValue = currentStep < 0 ? "— " : String(currentStep + 1).padStart(2, "0") + " ";
    for (const number of stepNumbers.children) {
      number.classList.toggle("is-playhead", Number(number.dataset.stepNumber) === currentStep);
    }
    for (const cell of trackRows.querySelectorAll(".step-toggle")) {
      cell.classList.toggle("is-playhead", Number(cell.dataset.step) === currentStep);
    }
  }

  function setPlayhead(step, token) {
    if (!playing || token !== playbackToken) return;
    currentStep = step;
    const wantedPage = Math.floor(step / STEPS_PER_PAGE);
    if (wantedPage !== page) renderSequencer();
    else renderPlayhead();
  }

  function updatePlaybackUI() {
    playButton.classList.toggle("is-playing", playing);
    playButton.setAttribute("aria-pressed", String(playing));
    playLabel.textContent = playing ? "СТОП" : "ИГРАТЬ";
    playState.textContent = playing ? "ИГРАЕТ" : "ГОТОВ";
    playState.classList.toggle("is-playing", playing);
  }

  class DrumAudio {
    constructor(AudioContextClass) {
      this.context = new AudioContextClass();
      this.master = this.context.createGain();
      this.limiter = this.context.createDynamicsCompressor();
      this.limiter.threshold.value = -10;
      this.limiter.knee.value = 14;
      this.limiter.ratio.value = 5;
      this.limiter.attack.value = 0.003;
      this.limiter.release.value = 0.17;
      this.master.gain.value = state.volume;
      this.master.connect(this.limiter);
      this.limiter.connect(this.context.destination);
      this.openHat = null;
      this.noiseBuffer = this.context.createBuffer(1, this.context.sampleRate, this.context.sampleRate);
      const samples = this.noiseBuffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) samples[i] = Math.random() * 2 - 1;
    }

    async unlock() {
      if (this.context.state !== "running") await this.context.resume();
    }

    setVolume(value) {
      const now = this.context.currentTime;
      this.master.gain.cancelScheduledValues(now);
      this.master.gain.setTargetAtTime(value, now, 0.025);
    }

    noiseVoice(time, options) {
      const source = this.context.createBufferSource();
      const filter = this.context.createBiquadFilter();
      const gain = this.context.createGain();
      source.buffer = this.noiseBuffer;
      filter.type = options.filter || "highpass";
      filter.frequency.setValueAtTime(options.frequency, time);
      filter.Q.setValueAtTime(options.q === undefined ? 0.7 : options.q, time);
      source.connect(filter);
      filter.connect(gain);
      gain.connect(this.master);
      return { source, filter, gain };
    }

    playKick(time) {
      const osc = this.context.createOscillator();
      const gain = this.context.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(155, time);
      osc.frequency.exponentialRampToValueAtTime(43, time + 0.14);
      gain.gain.setValueAtTime(0.0001, time);
      gain.gain.exponentialRampToValueAtTime(0.92, time + 0.006);
      gain.gain.exponentialRampToValueAtTime(0.0001, time + 0.34);
      osc.connect(gain);
      gain.connect(this.master);
      osc.start(time);
      osc.stop(time + 0.36);
    }

    playSnare(time) {
      const noise = this.noiseVoice(time, { filter: "highpass", frequency: 1250, q: 0.7 });
      noise.gain.gain.setValueAtTime(0.0001, time);
      noise.gain.gain.exponentialRampToValueAtTime(0.53, time + 0.004);
      noise.gain.gain.exponentialRampToValueAtTime(0.0001, time + 0.22);
      noise.source.start(time);
      noise.source.stop(time + 0.24);

      const body = this.context.createOscillator();
      const bodyGain = this.context.createGain();
      body.type = "triangle";
      body.frequency.setValueAtTime(190, time);
      body.frequency.exponentialRampToValueAtTime(94, time + 0.105);
      bodyGain.gain.setValueAtTime(0.0001, time);
      bodyGain.gain.exponentialRampToValueAtTime(0.28, time + 0.004);
      bodyGain.gain.exponentialRampToValueAtTime(0.0001, time + 0.14);
      body.connect(bodyGain);
      bodyGain.connect(this.master);
      body.start(time);
      body.stop(time + 0.16);
    }

    chokeOpenHat(time) {
      if (!this.openHat) return;
      try {
        const voice = this.openHat;
        voice.gain.gain.cancelScheduledValues(time);
        voice.gain.gain.setValueAtTime(Math.max(voice.gain.gain.value, 0.0001), time);
        voice.gain.gain.exponentialRampToValueAtTime(0.0001, time + 0.014);
        voice.source.stop(time + 0.02);
        this.openHat = null;
      } catch (_) {
        this.openHat = null;
      }
    }

    playHat(time, open) {
      if (!open) this.chokeOpenHat(time);
      const duration = open ? 0.43 : 0.055;
      const voice = this.noiseVoice(time, { filter: "highpass", frequency: open ? 5900 : 7200, q: 0.65 });
      voice.gain.gain.setValueAtTime(0.0001, time);
      voice.gain.gain.exponentialRampToValueAtTime(open ? 0.25 : 0.2, time + 0.003);
      voice.gain.gain.exponentialRampToValueAtTime(0.0001, time + duration);
      voice.source.start(time);
      voice.source.stop(time + duration + 0.025);
      if (open) {
        this.openHat = voice;
        voice.source.onended = () => { if (this.openHat === voice) this.openHat = null; };
      }
    }

    playClap(time) {
      const voice = this.noiseVoice(time, { filter: "bandpass", frequency: 1420, q: 0.75 });
      const envelope = voice.gain.gain;
      envelope.setValueAtTime(0.0001, time);
      [0, 0.013, 0.028, 0.047].forEach((offset, index) => {
        const start = time + offset;
        envelope.setValueAtTime(0.0001, start);
        envelope.linearRampToValueAtTime(index === 0 ? 0.46 : 0.35, start + 0.002);
        envelope.exponentialRampToValueAtTime(0.0001, start + 0.025);
      });
      voice.source.start(time);
      voice.source.stop(time + 0.13);
    }

    trigger(trackId, time) {
      switch (trackId) {
        case "kick": this.playKick(time); break;
        case "snare": this.playSnare(time); break;
        case "hatClosed": this.playHat(time, false); break;
        case "hatOpen": this.playHat(time, true); break;
        case "clap": this.playClap(time); break;
      }
    }
  }

  function getAudioEngine() {
    if (audioEngine) return audioEngine;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error("NO_WEB_AUDIO");
    audioEngine = new DrumAudio(Context);
    return audioEngine;
  }

  function schedule(token) {
    if (!playing || token !== playbackToken || !audioEngine) return;
    const context = audioEngine.context;
    if (context.state !== "running") return;
    const now = context.currentTime;
    if (nextStepTime < now - 0.3) nextStepTime = now + 0.025;
    const horizon = now + 0.13;
    while (nextStepTime < horizon) {
      const step = nextStep;
      const time = Math.max(nextStepTime, now + 0.005);
      for (const track of Engine.TRACKS) {
        if (state.steps[track.id][step]) audioEngine.trigger(track.id, time);
      }
      const delay = Math.max(0, (time - context.currentTime) * 1000);
      window.setTimeout(() => setPlayhead(step, token), delay);
      const sixteenth = 60 / state.tempo / 4;
      const swing = state.swing / 100;
      nextStepTime += sixteenth * (step % 2 === 0 ? 1 + swing : 1 - swing);
      nextStep = (step + 1) % Engine.STEP_COUNT;
    }
  }

  async function startPlayback() {
    if (starting || playing) return;
    starting = true;
    try {
      const audio = getAudioEngine();
      await audio.unlock();
      if (audio.context.state !== "running") throw new Error("AUDIO_SUSPENDED");
      audio.setVolume(state.volume);
      playing = true;
      currentStep = -1;
      page = 0;
      nextStep = 0;
      nextStepTime = audio.context.currentTime + 0.045;
      const token = ++playbackToken;
      updatePlaybackUI();
      renderSequencer();
      schedulerId = window.setInterval(() => schedule(token), 24);
      schedule(token);
    } catch (error) {
      playing = false;
      updatePlaybackUI();
      const message = error && error.message === "NO_WEB_AUDIO"
        ? "В этом браузере не поддерживается Web Audio."
        : "Не удалось включить звук — проверь настройки браузера.";
      showToast(message);
    } finally {
      starting = false;
    }
  }

  function stopPlayback() {
    if (schedulerId !== null) {
      window.clearInterval(schedulerId);
      schedulerId = null;
    }
    playbackToken++;
    playing = false;
    currentStep = -1;
    updatePlaybackUI();
    renderPlayhead();
  }

  async function audition(trackId) {
    try {
      const audio = getAudioEngine();
      await audio.unlock();
      if (audio.context.state !== "running") throw new Error("AUDIO_SUSPENDED");
      audio.setVolume(state.volume);
      audio.trigger(trackId, audio.context.currentTime + 0.008);
      const button = trackRows.querySelector('[data-audition="' + trackId + '"]');
      if (button) {
        button.classList.add("is-auditioning");
        window.setTimeout(() => button.classList.remove("is-auditioning"), 140);
      }
    } catch (_) {
      showToast("Коснись ещё раз, чтобы разрешить звук в браузере.");
    }
  }

  function setTempo(value) {
    state = { ...state, tempo: Engine.clamp(Math.round(Number(value) || 60), 60, 180) };
    updateControls();
    queueSave();
  }

  function choosePreset(id) {
    if (!Object.prototype.hasOwnProperty.call(Engine.PATTERNS, id)) return;
    const previousVolume = state.volume;
    state = Engine.createPreset(id);
    state.volume = previousVolume;
    page = playing && currentStep >= STEPS_PER_PAGE ? 1 : 0;
    updateControls();
    updatePatternDetails();
    renderSequencer();
    queueSave();
    showToast("Ритм «" + Engine.PATTERNS[id].name + "» загружен");
  }

  function clearPattern() {
    state = { ...state, preset: "custom", steps: Engine.blankSteps() };
    updatePatternDetails();
    renderSequencer();
    queueSave();
    showToast("Все шаги очищены");
  }

  function randomizePattern() {
    state = Engine.randomizeState(state);
    page = 0;
    updatePatternDetails();
    renderSequencer();
    queueSave();
    showToast("Новый ритм готов — нажми играть");
  }

  function changePage(nextPage) {
    page = Engine.clamp(Number(nextPage) || 0, 0, 1);
    renderSequencer();
  }

  function updateSettingsFromInput() {
    const swing = $("#swingRange");
    const volume = $("#volumeRange");
    state = {
      ...state,
      swing: Engine.clamp(Number(swing.value), 0, 60),
      volume: Engine.clamp(Number(volume.value) / 100, 0, 1)
    };
    $("#swingValue").textContent = state.swing + "%";
    $("#volumeValue").textContent = Math.round(state.volume * 100) + "%";
    updateRangeFill(swing);
    updateRangeFill(volume);
    if (audioEngine) audioEngine.setVolume(state.volume);
    queueSave();
  }

  function tapTempo() {
    const now = performance.now();
    if (tapTimes.length && now - tapTimes[tapTimes.length - 1] > 2200) tapTimes.length = 0;
    tapTimes.push(now);
    if (tapTimes.length > 5) tapTimes.shift();
    if (tapTimes.length < 2) {
      showToast("Тапни ещё раз в такт");
      return;
    }
    const intervals = [];
    for (let i = 1; i < tapTimes.length; i++) intervals.push(tapTimes[i] - tapTimes[i - 1]);
    const average = intervals.reduce((sum, value) => sum + value, 0) / intervals.length;
    setTempo(60000 / average);
    showToast("Темп: " + state.tempo + " BPM");
  }

  function openInstallDialog() {
    const dialog = $("#installDialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else showToast("Safari: Поделиться → На экран Домой · Android: меню → Установить");
  }

  loadPresetButtons();
  updateControls();
  updatePatternDetails();
  renderSequencer();
  updatePlaybackUI();
  updateSaveStatus("", STORAGE.restored ? "СОХРАНЕНО" : "ГОТОВО");

  playButton.addEventListener("click", () => playing ? stopPlayback() : startPlayback());
  $("#presetList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-preset]");
    if (button) choosePreset(button.dataset.preset);
  });
  $("#trackRows").addEventListener("click", (event) => {
    const auditionButton = event.target.closest("[data-audition]");
    if (auditionButton) {
      audition(auditionButton.dataset.audition);
      return;
    }
    const stepButton = event.target.closest(".step-toggle");
    if (!stepButton) return;
    state = Engine.toggleStep(state, stepButton.dataset.track, Number(stepButton.dataset.step));
    updatePatternDetails();
    renderSequencer();
    queueSave();
  });
  document.querySelectorAll(".page-switch [data-page]").forEach((button) => {
    button.addEventListener("click", () => changePage(button.dataset.page));
  });
  $("#tempoRange").addEventListener("input", (event) => setTempo(event.currentTarget.value));
  $("#tempoDown").addEventListener("click", () => setTempo(state.tempo - 1));
  $("#tempoUp").addEventListener("click", () => setTempo(state.tempo + 1));
  $("#tapButton").addEventListener("click", tapTempo);
  $("#swingRange").addEventListener("input", updateSettingsFromInput);
  $("#volumeRange").addEventListener("input", updateSettingsFromInput);
  $("#clearButton").addEventListener("click", clearPattern);
  $("#randomButton").addEventListener("click", randomizePattern);

  $("#installButton").addEventListener("click", async () => {
    if (!installPrompt) {
      openInstallDialog();
      return;
    }
    const prompt = installPrompt;
    installPrompt = null;
    try {
      await prompt.prompt();
      const result = await prompt.userChoice;
      if (result.outcome === "accepted") showToast("Witchbeat устанавливается");
      else showToast("Установку можно запустить позже");
    } catch (_) {
      openInstallDialog();
    }
  });
  $("#installDialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
  });
  window.addEventListener("appinstalled", () => showToast("Witchbeat добавлен на главный экран"));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && playing) stopPlayback();
  });
  window.addEventListener("pagehide", saveNow);
})();
