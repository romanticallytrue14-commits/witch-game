/* Minimal PCM16 WAV encoder for Witchbeat's OfflineAudioContext renders. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WitchbeatWav = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function encodeWav(audioBuffer) {
    if (!audioBuffer || typeof audioBuffer.getChannelData !== "function") {
      throw new TypeError("Expected an AudioBuffer-like object.");
    }
    const channels = Math.max(1, Math.floor(audioBuffer.numberOfChannels));
    const frames = Math.max(0, Math.floor(audioBuffer.length));
    const sampleRate = Math.floor(audioBuffer.sampleRate);
    if (!Number.isFinite(sampleRate) || sampleRate < 1) throw new RangeError("Invalid sample rate.");

    const bytesPerSample = 2;
    const dataBytes = frames * channels * bytesPerSample;
    if (dataBytes > 0xffffffff - 36) throw new RangeError("WAV is too large.");
    const output = new ArrayBuffer(44 + dataBytes);
    const view = new DataView(output);
    const writeText = (offset, text) => {
      for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
    };

    writeText(0, "RIFF");
    view.setUint32(4, 36 + dataBytes, true);
    writeText(8, "WAVE");
    writeText(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, channels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * channels * bytesPerSample, true);
    view.setUint16(32, channels * bytesPerSample, true);
    view.setUint16(34, 16, true);
    writeText(36, "data");
    view.setUint32(40, dataBytes, true);

    const channelData = Array.from({ length: channels }, (_, channel) => audioBuffer.getChannelData(channel));
    let offset = 44;
    for (let frame = 0; frame < frames; frame++) {
      for (let channel = 0; channel < channels; channel++) {
        const raw = channelData[channel][frame];
        const sample = Number.isFinite(raw) ? Math.max(-1, Math.min(1, raw)) : 0;
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += bytesPerSample;
      }
    }
    return output;
  }

  return Object.freeze({ encodeWav });
});
