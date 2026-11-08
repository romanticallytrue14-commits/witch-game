(function () {
  "use strict";

  const Engine = window.DrumMachineEngine;
  const $ = (selector, root = document) => root.querySelector(selector);
  const STORAGE_KEY = "witchbeat-pattern-v1";
  const STORAGE = (() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return { state: raw ? Engine.sanitizeState(JSON.parse(raw)) : Engine.createPreset("midnight"), restored: Boolean(raw) };
    } catch (_) {
      return { state: Engine.createPreset("midnight"), restored: false };
    }
  })();

  let state = STORAGE.state;
  let currentStep = -1;
  let notePressTimer = null;
  let longPressNoteCell = null;
  let preparedWavUrl = null;
  let preparedWavSignature = null;
  let renderingWav = false;
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
  const WAV_REPEAT_COUNT = 4;
  const WAV_START_OFFSET = 0.05;
  const WAV_TAIL_SECONDS = 1.1;
  const NOTE_DURATION_STEPS = Object.freeze({ tb303: 0.82, piano: 2.4, organ: 1.5, strings: 2.8, bell: 2.1 });

  const playButton = $("#playButton");
  const playLabel = $("#playLabel");
  const playState = $("#playState");
  const stepReadout = $("#stepReadout");
  const beatRuler = $("#beatRuler");
  const stepNumbers = $("#stepNumbers");
  const trackRows = $("#trackRows");
  const patternDescription = $("#patternDescription");
  const saveStatus = $("#saveStatus");
  const toast = $("#toast");
  const exportLink = $("#exportLink");
  const exportLabel = $("#exportLabel");

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

  function invalidatePreparedWav() {
    if (!preparedWavUrl) return;
    if (window.URL && typeof window.URL.revokeObjectURL === "function") window.URL.revokeObjectURL(preparedWavUrl);
    preparedWavUrl = null;
    preparedWavSignature = null;
    exportLink.href = "#export-wav";
    exportLink.removeAttribute("download");
    exportLink.classList.remove("is-ready");
    exportLink.setAttribute("aria-label", "Подготовить текущий трек к скачиванию в WAV");
    exportLabel.textContent = "WAV";
  }

  function queueSave() {
    invalidatePreparedWav();
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
    const cutoff = $("#tbCutoffRange");
    const resonance = $("#tbResonanceRange");
    tempo.value = String(state.tempo);
    $("#tempoValue").textContent = String(state.tempo);
    swing.value = String(state.swing);
    $("#swingValue").textContent = state.swing + "%";
    volume.value = String(Math.round(state.volume * 100));
    $("#volumeValue").textContent = Math.round(state.volume * 100) + "%";
    cutoff.value = String(state.tb303Cutoff);
    $("#tbCutoffValue").textContent = state.tb303Cutoff >= 1000
      ? (state.tb303Cutoff / 1000).toFixed(state.tb303Cutoff % 1000 ? 1 : 0) + " kHz"
      : state.tb303Cutoff + " Hz";
    resonance.value = String(state.tb303Resonance);
    $("#tbResonanceValue").textContent = state.tb303Resonance + "%";
    for (const input of [tempo, swing, volume, cutoff, resonance]) updateRangeFill(input);
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

  function renderSequencer() {
    beatRuler.replaceChildren();
    for (let beat = 0; beat < 4; beat++) {
      const label = document.createElement("span");
      label.className = "beat-label";
      label.style.gridColumn = "span 4";
      label.innerHTML = '<b></b><small>1/4</small>';
      label.querySelector("b").textContent = String(beat + 1);
      beatRuler.appendChild(label);
    }

    stepNumbers.replaceChildren();
    for (let step = 0; step < Engine.STEP_COUNT; step++) {
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
      row.className = "track-row" + (track.synth ? " note-track" : "");
      row.dataset.track = track.id;
      row.style.setProperty("--track-color", track.color);

      const meta = document.createElement("div");
      meta.className = "track-meta";
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
      meta.appendChild(audition);
      const metaHint = document.createElement("span");
      metaHint.className = "track-meta-hint";
      metaHint.textContent = track.synth ? "УДЕРЖИВАЙ ШАГ → НОТА" : "ТАП → ПРОСЛУШАТЬ";
      meta.appendChild(metaHint);
      row.appendChild(meta);

      const cells = document.createElement("div");
      cells.className = "step-cells";
      for (let step = 0; step < Engine.STEP_COUNT; step++) {
        const active = state.steps[track.id][step] === true;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "step-toggle" + (track.synth ? " note-step" : "") + (track.id === "tb303" ? " tb303-step" : "") + (active ? " is-active" : "") + (step === currentStep ? " is-playhead" : "");
        button.dataset.track = track.id;
        button.dataset.step = String(step);
        button.setAttribute("aria-pressed", String(active));
        let label = "Шаг " + (step + 1) + ": " + track.name + ", " + (active ? "включён" : "выключен");
        if (track.synth) {
          const field = Engine.NOTE_FIELDS[track.id];
          const note = Engine.NOTE_SCALES[track.id][state[field][step]];
          if (active) button.textContent = note.short;
          label += active ? ", нота " + note.name + ". Удерживай, чтобы сменить." : ". Удерживай, чтобы включить и выбрать ноту.";
          button.title = "Шаг " + (step + 1) + " · " + track.name + " · " + (active ? note.name : "выкл.") + " · удерживай для смены ноты";
        } else {
          button.title = "Шаг " + (step + 1) + " · " + track.name;
        }
        button.setAttribute("aria-label", label);
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
    renderPlayhead();
  }

  function updatePlaybackUI() {
    playButton.classList.toggle("is-playing", playing);
    playButton.setAttribute("aria-pressed", String(playing));
    playLabel.textContent = playing ? "СТОП" : "ИГРАТЬ";
    playState.textContent = playing ? "ИГРАЕТ" : "ГОТОВ";
    playState.classList.toggle("is-playing", playing);
  }

  class DrumAudio {
    constructor(AudioContextClassOrInstance) {
      this.context = typeof AudioContextClassOrInstance === "function"
        ? new AudioContextClassOrInstance()
        : AudioContextClassOrInstance;
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
      const context = this.context;
      if (context.state === "closed") throw new Error("AUDIO_CLOSED");
      if (context.state !== "running") {
        let resumePromise;
        try {
          // Invoke resume synchronously during the user's tap; mobile browsers
          // may reject it if it is deferred to a later task.
          resumePromise = context.resume();
        } catch (error) {
          this.primeOutput();
          throw error;
        }
        this.primeOutput();
        await resumePromise;
      }
      if (context.state !== "running") throw new Error("AUDIO_SUSPENDED");
    }

    primeOutput() {
      try {
        const context = this.context;
        const duration = 0.04;
        const buffer = context.createBuffer(1, Math.max(1, Math.round(context.sampleRate * duration)), context.sampleRate);
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        source.start(context.currentTime);
      } catch (_) {
        // The silent warm-up is a legacy WebKit fallback; resume remains primary.
      }
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

    playTb303(time, settings = {}) {
      const midi = Number.isFinite(settings.midi) ? settings.midi : Engine.TB303_NOTES[0].midi;
      const cutoffValue = Number.isFinite(settings.cutoff) ? settings.cutoff : state.tb303Cutoff;
      const resonanceValue = Number.isFinite(settings.resonance) ? settings.resonance : state.tb303Resonance;
      const durationValue = Number.isFinite(settings.duration) ? settings.duration : 0.18;
      const cutoff = Engine.clamp(cutoffValue, 250, 8500);
      const resonance = Engine.clamp(resonanceValue, 0, 100);
      const duration = Engine.clamp(durationValue, 0.06, 0.8);
      const accent = Boolean(settings.accent);
      const frequency = 440 * Math.pow(2, (midi - 69) / 12);
      const osc = this.context.createOscillator();
      const filter = this.context.createBiquadFilter();
      const amp = this.context.createGain();
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(frequency, time);
      filter.type = "lowpass";
      filter.frequency.setValueAtTime(Math.min(12000, cutoff * (accent ? 2.25 : 1.65)), time);
      filter.frequency.exponentialRampToValueAtTime(Math.max(80, cutoff * 0.58), time + Math.min(0.16, duration * 0.72));
      filter.Q.setValueAtTime(0.7 + resonance * 0.17, time);
      amp.gain.setValueAtTime(0.0001, time);
      amp.gain.exponentialRampToValueAtTime(accent ? 0.48 : 0.32, time + 0.005);
      amp.gain.exponentialRampToValueAtTime(0.0001, time + duration);
      osc.connect(filter);
      filter.connect(amp);
      amp.connect(this.master);
      osc.start(time);
      osc.stop(time + duration + 0.02);
    }

    playPartials(time, settings, partials, defaultDuration, attack) {
      const midi = Number.isFinite(settings.midi) ? settings.midi : Engine.NOTE_SCALES.piano[0].midi;
      const durationValue = Number.isFinite(settings.duration) ? settings.duration : defaultDuration;
      const duration = Engine.clamp(durationValue, 0.08, 1.5);
      const fundamental = 440 * Math.pow(2, (midi - 69) / 12);
      for (const partial of partials) {
        const osc = this.context.createOscillator();
        const amp = this.context.createGain();
        osc.type = partial.type || "sine";
        osc.frequency.setValueAtTime(fundamental * partial.ratio, time);
        amp.gain.setValueAtTime(0.0001, time);
        amp.gain.exponentialRampToValueAtTime(partial.level, time + Math.min(attack, duration * 0.35));
        amp.gain.exponentialRampToValueAtTime(0.0001, time + duration * partial.decay);
        osc.connect(amp);
        amp.connect(this.master);
        osc.start(time);
        osc.stop(time + duration + 0.025);
      }
    }

    playPiano(time, settings = {}) {
      this.playPartials(time, settings, [
        { ratio: 1, level: 0.2, decay: 1 },
        { ratio: 2.01, level: 0.075, decay: 0.58 },
        { ratio: 3.96, level: 0.032, decay: 0.34 }
      ], 0.46, 0.004);
    }

    playOrgan(time, settings = {}) {
      this.playPartials(time, settings, [
        { ratio: 1, level: 0.14, decay: 1 },
        { ratio: 2, level: 0.06, decay: 1 },
        { ratio: 3, level: 0.025, decay: 1 }
      ], 0.34, 0.016);
    }

    playStrings(time, settings = {}) {
      const midi = Number.isFinite(settings.midi) ? settings.midi : Engine.NOTE_SCALES.strings[0].midi;
      const durationValue = Number.isFinite(settings.duration) ? settings.duration : 0.58;
      const duration = Engine.clamp(durationValue, 0.12, 1.5);
      const frequency = 440 * Math.pow(2, (midi - 69) / 12);
      const oscA = this.context.createOscillator();
      const oscB = this.context.createOscillator();
      const filter = this.context.createBiquadFilter();
      const amp = this.context.createGain();
      oscA.type = "sawtooth";
      oscB.type = "sawtooth";
      oscA.frequency.setValueAtTime(frequency, time);
      oscB.frequency.setValueAtTime(frequency * 1.004, time);
      filter.type = "lowpass";
      filter.frequency.setValueAtTime(2800, time);
      filter.Q.setValueAtTime(0.7, time);
      amp.gain.setValueAtTime(0.0001, time);
      amp.gain.exponentialRampToValueAtTime(0.115, time + Math.min(0.055, duration * 0.25));
      amp.gain.setValueAtTime(0.09, time + duration * 0.72);
      amp.gain.exponentialRampToValueAtTime(0.0001, time + duration);
      oscA.connect(filter);
      oscB.connect(filter);
      filter.connect(amp);
      amp.connect(this.master);
      oscA.start(time);
      oscB.start(time);
      oscA.stop(time + duration + 0.03);
      oscB.stop(time + duration + 0.03);
    }

    playBell(time, settings = {}) {
      this.playPartials(time, settings, [
        { ratio: 1, level: 0.15, decay: 1 },
        { ratio: 2.76, level: 0.06, decay: 0.52 },
        { ratio: 4.08, level: 0.022, decay: 0.28 }
      ], 0.72, 0.002);
    }

    trigger(trackId, time, settings = {}) {
      switch (trackId) {
        case "kick": this.playKick(time); break;
        case "snare": this.playSnare(time); break;
        case "hatClosed": this.playHat(time, false); break;
        case "hatOpen": this.playHat(time, true); break;
        case "clap": this.playClap(time); break;
        case "tb303": this.playTb303(time, settings); break;
        case "piano": this.playPiano(time, settings); break;
        case "organ": this.playOrgan(time, settings); break;
        case "strings": this.playStrings(time, settings); break;
        case "bell": this.playBell(time, settings); break;
      }
    }
  }

  function getAudioEngine() {
    if (audioEngine && audioEngine.context.state !== "closed") return audioEngine;
    audioEngine = null;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error("NO_WEB_AUDIO");
    audioEngine = new DrumAudio(Context);
    return audioEngine;
  }

  function usePlaybackAudioSession() {
    try {
      const session = navigator.audioSession;
      if (session && "type" in session) session.type = "playback";
    } catch (_) {
      // AudioSession is not implemented in every browser.
    }
  }

  function primeAudioFromGesture() {
    usePlaybackAudioSession();
    try {
      getAudioEngine().unlock().catch(() => {});
    } catch (_) {
      // Playback gives the user a visible error if this browser lacks audio.
    }
  }

  function getStepDuration(pattern, step) {
    const sixteenth = 60 / pattern.tempo / 4;
    const swing = pattern.swing / 100;
    return sixteenth * (step % 2 === 0 ? 1 + swing : 1 - swing);
  }

  function triggerStep(audio, pattern, step, time, stepDuration) {
    for (const track of Engine.TRACKS) {
      if (!pattern.steps[track.id][step]) continue;
      if (!track.synth) {
        audio.trigger(track.id, time);
        continue;
      }
      const field = Engine.NOTE_FIELDS[track.id];
      const note = Engine.NOTE_SCALES[track.id][pattern[field][step]];
      const settings = {
        midi: note.midi,
        duration: stepDuration * (NOTE_DURATION_STEPS[track.id] || 1),
        accent: step % 4 === 0
      };
      if (track.id === "tb303") {
        settings.cutoff = pattern.tb303Cutoff;
        settings.resonance = pattern.tb303Resonance;
      }
      audio.trigger(track.id, time, settings);
    }
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
      const stepDuration = getStepDuration(state, step);
      triggerStep(audioEngine, state, step, time, stepDuration);
      const delay = Math.max(0, (time - context.currentTime) * 1000);
      window.setTimeout(() => setPlayhead(step, token), delay);
      nextStepTime += stepDuration;
      nextStep = (step + 1) % Engine.STEP_COUNT;
    }
  }

  async function renderWavBlob(pattern) {
    const OfflineContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!OfflineContext || !window.WitchbeatWav) throw new Error("NO_WAV_EXPORT");
    const sampleRate = audioEngine && audioEngine.context.sampleRate ? audioEngine.context.sampleRate : 44100;
    const barDuration = 240 / pattern.tempo;
    const frameCount = Math.ceil((WAV_START_OFFSET + barDuration * WAV_REPEAT_COUNT + WAV_TAIL_SECONDS) * sampleRate);
    const context = new OfflineContext(2, frameCount, sampleRate);
    const renderer = new DrumAudio(context);
    renderer.master.gain.value = pattern.volume;

    let time = WAV_START_OFFSET;
    for (let bar = 0; bar < WAV_REPEAT_COUNT; bar++) {
      for (let step = 0; step < Engine.STEP_COUNT; step++) {
        const stepDuration = getStepDuration(pattern, step);
        triggerStep(renderer, pattern, step, time, stepDuration);
        time += stepDuration;
      }
    }

    const audioBuffer = await context.startRendering();
    return new Blob([window.WitchbeatWav.encodeWav(audioBuffer)], { type: "audio/wav" });
  }

  function exportWavFilename(pattern) {
    const groove = pattern.preset && pattern.preset !== "custom" ? pattern.preset : "custom";
    return "witchbeat-" + groove + "-4bars.wav";
  }

  async function prepareWavDownload(event) {
    const signature = JSON.stringify(state);
    if (preparedWavUrl && preparedWavSignature === signature) return;
    event.preventDefault();
    if (renderingWav) return;
    if (!Engine.TRACKS.some((track) => state.steps[track.id].some(Boolean))) {
      showToast("Сетка пустая — сначала добавь шаги.");
      return;
    }
    if (state.volume <= 0) {
      showToast("Громкость приложения на нуле — WAV будет беззвучным.");
      return;
    }
    if (!window.OfflineAudioContext && !window.webkitOfflineAudioContext) {
      showToast("Этот браузер не поддерживает экспорт WAV.");
      return;
    }

    renderingWav = true;
    exportLink.setAttribute("aria-busy", "true");
    exportLabel.textContent = "РЕНДЕР…";
    showToast("Собираю четыре такта в WAV…");
    try {
      const snapshot = Engine.sanitizeState(state);
      const blob = await renderWavBlob(snapshot);
      if (JSON.stringify(state) !== signature) {
        showToast("Паттерн изменился во время рендера — сохрани WAV ещё раз.");
        return;
      }
      invalidatePreparedWav();
      preparedWavUrl = window.URL.createObjectURL(blob);
      preparedWavSignature = signature;
      exportLink.href = preparedWavUrl;
      exportLink.download = exportWavFilename(snapshot);
      exportLink.classList.add("is-ready");
      exportLink.setAttribute("aria-label", "Скачать текущий трек в WAV");
      exportLabel.textContent = "СКАЧАТЬ WAV";
      showToast("WAV готов — нажми «Скачать WAV» ещё раз.");
    } catch (error) {
      showToast(error && error.message === "NO_WAV_EXPORT"
        ? "Не удалось подготовить WAV в этом браузере."
        : "Не получилось собрать WAV — попробуй ещё раз.");
    } finally {
      renderingWav = false;
      exportLink.removeAttribute("aria-busy");
      if (!preparedWavUrl) exportLabel.textContent = "WAV";
    }
  }

  async function startPlayback() {
    if (starting || playing) return;
    if (!Engine.TRACKS.some((track) => state.steps[track.id].some(Boolean))) {
      showToast("Сетка пустая — выбери ритм или включи шаги.");
      return;
    }
    if (state.volume <= 0) {
      showToast("Громкость приложения на нуле — подними ползунок.");
      return;
    }
    starting = true;
    try {
      usePlaybackAudioSession();
      const audio = getAudioEngine();
      await audio.unlock();
      if (audio.context.state !== "running") throw new Error("AUDIO_SUSPENDED");
      audio.setVolume(state.volume);
      playing = true;
      currentStep = -1;
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
        : error && error.message === "AUDIO_SUSPENDED"
          ? "Браузер не включил звук. Нажми «Играть» ещё раз и проверь громкость устройства."
          : "Не удалось включить звук — проверь настройки браузера и громкость устройства.";
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
    if (state.volume <= 0) {
      showToast("Громкость приложения на нуле — подними ползунок.");
      return;
    }
    try {
      usePlaybackAudioSession();
      const audio = getAudioEngine();
      await audio.unlock();
      if (audio.context.state !== "running") throw new Error("AUDIO_SUSPENDED");
      audio.setVolume(state.volume);
      const when = audio.context.currentTime + 0.008;
      const track = Engine.TRACKS.find((candidate) => candidate.id === trackId);
      if (track && track.synth) {
        const step = currentStep >= 0 ? currentStep : 0;
        const field = Engine.NOTE_FIELDS[trackId];
        const note = Engine.NOTE_SCALES[trackId][state[field][step]];
        const settings = {
          midi: note.midi,
          duration: Math.max(0.22, 0.12 * (NOTE_DURATION_STEPS[trackId] || 1)),
          accent: true
        };
        if (trackId === "tb303") {
          settings.cutoff = state.tb303Cutoff;
          settings.resonance = state.tb303Resonance;
        }
        audio.trigger(trackId, when, settings);
      } else {
        audio.trigger(trackId, when);
      }
      const button = trackRows.querySelector('[data-audition="' + trackId + '"]');
      if (button) {
        button.classList.add("is-auditioning");
        window.setTimeout(() => button.classList.remove("is-auditioning"), 140);
      }
    } catch (_) {
      showToast("Браузер заблокировал звук. Коснись «Играть» ещё раз и проверь громкость устройства.");
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
    updatePatternDetails();
    renderSequencer();
    queueSave();
    showToast("Новый ритм готов — нажми играть");
  }

  function cycleInstrumentStep(trackId, step) {
    const track = Engine.TRACKS.find((candidate) => candidate.id === trackId);
    if (!track || !track.synth) return;
    state = Engine.cycleInstrumentNote(state, trackId, step);
    const field = Engine.NOTE_FIELDS[trackId];
    const note = Engine.NOTE_SCALES[trackId][state[field][step]];
    const cell = trackRows.querySelector('[data-track="' + trackId + '"][data-step="' + step + '"]');
    if (cell) {
      cell.textContent = note.short;
      cell.classList.add("is-active");
      cell.setAttribute("aria-pressed", "true");
      cell.setAttribute("aria-label", "Шаг " + (step + 1) + ": " + track.name + ", нота " + note.name + ". Удерживай, чтобы сменить.");
      cell.title = "Шаг " + (step + 1) + " · " + track.name + " · " + note.name + " · удерживай для смены ноты";
    }
    updatePatternDetails();
    queueSave();
    longPressNoteCell = trackId + ":" + step;
    showToast(track.short + " · " + note.name);
  }

  function clearNotePress() {
    if (notePressTimer !== null) {
      window.clearTimeout(notePressTimer);
      notePressTimer = null;
    }
  }

  function updateSettingsFromInput() {
    const swing = $("#swingRange");
    const volume = $("#volumeRange");
    const cutoff = $("#tbCutoffRange");
    const resonance = $("#tbResonanceRange");
    state = {
      ...state,
      swing: Engine.clamp(Number(swing.value), 0, 60),
      volume: Engine.clamp(Number(volume.value) / 100, 0, 1),
      tb303Cutoff: Engine.clamp(Number(cutoff.value), 250, 8500),
      tb303Resonance: Engine.clamp(Number(resonance.value), 0, 100)
    };
    $("#swingValue").textContent = state.swing + "%";
    $("#volumeValue").textContent = Math.round(state.volume * 100) + "%";
    $("#tbCutoffValue").textContent = state.tb303Cutoff >= 1000
      ? (state.tb303Cutoff / 1000).toFixed(state.tb303Cutoff % 1000 ? 1 : 0) + " kHz"
      : state.tb303Cutoff + " Hz";
    $("#tbResonanceValue").textContent = state.tb303Resonance + "%";
    for (const input of [swing, volume, cutoff, resonance]) updateRangeFill(input);
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

  const audioGestureEvent = window.PointerEvent ? "pointerdown" : "touchstart";
  playButton.addEventListener(audioGestureEvent, primeAudioFromGesture, { passive: true });
  trackRows.addEventListener(audioGestureEvent, (event) => {
    if (event.target && typeof event.target.closest === "function" && event.target.closest("[data-audition]")) {
      primeAudioFromGesture();
    }
  }, { passive: true });
  playButton.addEventListener("click", () => playing ? stopPlayback() : startPlayback());
  $("#presetList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-preset]");
    if (button) choosePreset(button.dataset.preset);
  });
  trackRows.addEventListener("pointerdown", (event) => {
    const stepButton = event.target.closest(".note-step");
    if (!stepButton) return;
    longPressNoteCell = null;
    clearNotePress();
    const step = Number(stepButton.dataset.step);
    const trackId = stepButton.dataset.track;
    notePressTimer = window.setTimeout(() => {
      notePressTimer = null;
      cycleInstrumentStep(trackId, step);
    }, 520);
  });
  window.addEventListener("pointerup", clearNotePress);
  window.addEventListener("pointercancel", clearNotePress);
  trackRows.addEventListener("click", (event) => {
    const auditionButton = event.target.closest("[data-audition]");
    if (auditionButton) {
      audition(auditionButton.dataset.audition);
      return;
    }
    const stepButton = event.target.closest(".step-toggle");
    if (!stepButton) return;
    if (longPressNoteCell !== null) {
      const shouldSuppress = longPressNoteCell === stepButton.dataset.track + ":" + stepButton.dataset.step;
      longPressNoteCell = null;
      if (shouldSuppress) return;
    }
    state = Engine.toggleStep(state, stepButton.dataset.track, Number(stepButton.dataset.step));
    updatePatternDetails();
    renderSequencer();
    queueSave();
  });
  $("#tempoRange").addEventListener("input", (event) => setTempo(event.currentTarget.value));
  $("#tempoDown").addEventListener("click", () => setTempo(state.tempo - 1));
  $("#tempoUp").addEventListener("click", () => setTempo(state.tempo + 1));
  $("#tapButton").addEventListener("click", tapTempo);
  $("#swingRange").addEventListener("input", updateSettingsFromInput);
  $("#volumeRange").addEventListener("input", updateSettingsFromInput);
  $("#tbCutoffRange").addEventListener("input", updateSettingsFromInput);
  $("#tbResonanceRange").addEventListener("input", updateSettingsFromInput);
  $("#clearButton").addEventListener("click", clearPattern);
  $("#randomButton").addEventListener("click", randomizePattern);
  exportLink.addEventListener("click", prepareWavDownload);

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
