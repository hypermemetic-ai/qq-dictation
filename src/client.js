(() => {
  "use strict";

  const PREFIX = "/qq/dictate";
  const TARGET_RATE = 16_000;
  let capture = null;
  let recording = false;
  let starting = false;
  let pollTimer = 0;

  const pageSessionId = () => {
    const composer = document.querySelector("#composer");
    const fromComposer = composer?.dataset.sessionId;
    if (fromComposer) return fromComposer;
    const match = location.pathname.match(/\/session\/(session-[0-9a-fA-F-]{36})(?:\/|$)/);
    return match ? match[1] : "";
  };

  const dictateButton = () => document.querySelector("#composer-dictate");

  const paint = () => {
    const button = dictateButton();
    if (!button) return;
    button.setAttribute("aria-label", recording ? "Cancel dictation" : "Dictate");
    button.dataset.state = recording ? "recording" : "idle";
    document.querySelector("#composer")?.classList.toggle("is-dictating", recording);
  };

  const postJson = async (path, body) => {
    const response = await fetch(`${PREFIX}${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      const error = new Error(payload.error || `dictation ${path} failed`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  };

  const readStatus = async () => {
    const response = await fetch(`${PREFIX}/`, { credentials: "same-origin" });
    if (!response.ok) return { state: "idle" };
    try {
      return await response.json();
    } catch {
      return { state: "idle" };
    }
  };

  const noteFocus = () => {
    const sessionId = pageSessionId();
    if (!sessionId) return;
    void postJson("/focus", { sessionId }).catch(() => {});
  };

  const downsample = (input, fromRate, toRate) => {
    if (!input || !input.length) return new Float32Array(0);
    if (fromRate === toRate) return input;
    const ratio = fromRate / toRate;
    const outLen = Math.max(0, Math.round(input.length / ratio));
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(input.length, Math.floor((i + 1) * ratio) || start + 1);
      let sum = 0;
      let count = 0;
      for (let j = start; j < end; j += 1) {
        sum += input[j];
        count += 1;
      }
      out[i] = count ? sum / count : input[Math.min(start, input.length - 1)] || 0;
    }
    return out;
  };

  const floatToPcm16 = (input) => {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, input[i] || 0));
      out[i] = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff);
    }
    return out;
  };

  const encodeWav = (pcm, sampleRate) => {
    const dataSize = pcm.byteLength;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);
    const writeAscii = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
    };
    writeAscii(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeAscii(8, "WAVE");
    writeAscii(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeAscii(36, "data");
    view.setUint32(40, dataSize, true);
    new Uint8Array(buffer, 44).set(new Uint8Array(pcm.buffer, pcm.byteOffset, dataSize));
    return new Blob([buffer], { type: "audio/wav" });
  };

  const collectWav = (live) => {
    const chunks = live?.pcmChunks ?? [];
    let total = 0;
    for (const chunk of chunks) total += chunk.length;
    const pcm = new Int16Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      pcm.set(chunk, offset);
      offset += chunk.length;
    }
    return encodeWav(pcm, TARGET_RATE);
  };

  const stopCapture = async () => {
    const live = capture;
    capture = null;
    if (!live) return null;
    try { live.source?.disconnect?.(); } catch {}
    try { live.processor?.disconnect?.(); } catch {}
    try { live.mute?.disconnect?.(); } catch {}
    try { live.stream?.getTracks?.().forEach((track) => track.stop()); } catch {}
    try { await live.context?.close?.(); } catch {}
    return live;
  };

  const setRecording = (next) => {
    recording = next;
    paint();
  };

  const startMic = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("qq-dictation: microphone is unavailable");
    }
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) throw new Error("qq-dictation: audio context is unavailable");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
    let context;
    try {
      context = new AudioCtx({ sampleRate: TARGET_RATE });
    } catch {
      context = new AudioCtx();
    }
    if (context.state === "suspended") await context.resume();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const mute = context.createGain();
    mute.gain.value = 0;
    const live = {
      stream,
      context,
      source,
      processor,
      mute,
      pcmChunks: [],
    };
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const resampled = downsample(input, context.sampleRate || TARGET_RATE, TARGET_RATE);
      const pcm = floatToPcm16(resampled);
      if (pcm.length) live.pcmChunks.push(pcm);
    };
    source.connect(processor);
    processor.connect(mute);
    mute.connect(context.destination);
    capture = live;
  };

  const start = async (bindSessionId) => {
    if (recording || starting) return;
    starting = true;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("qq-dictation: microphone is unavailable");
      }
      await startMic();
      setRecording(true);
      const status = await readStatus();
      if (status.state === "recording") return;
      const sessionId = bindSessionId || "";
      await postJson("/start", sessionId ? { sessionId } : {});
    } catch {
      await stopCapture();
      try { await postJson("/cancel", {}); } catch {}
      setRecording(false);
    } finally {
      starting = false;
    }
  };

  const end = async () => {
    const status = recording ? { state: "recording" } : await readStatus();
    if (status.state !== "recording" && !recording) return;
    setRecording(false);
    const live = await stopCapture();
    const wav = collectWav(live);
    try {
      await fetch(`${PREFIX}/end`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "audio/wav" },
        body: wav,
      });
    } catch {
      try { await postJson("/cancel", {}); } catch {}
    }
  };

  const cancel = async () => {
    setRecording(false);
    await stopCapture();
    try { await postJson("/cancel", {}); } catch {}
  };

  const isPrompt = (node) => node && node.id === "prompt";
  const closest = (node, selector) => (node && typeof node.closest === "function" ? node.closest(selector) : null);

  document.addEventListener("focusin", (event) => {
    if (isPrompt(event.target)) noteFocus();
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    const dictate = closest(target, "#composer-dictate");
    if (dictate) {
      event.preventDefault();
      if (recording) void cancel();
      else void start(pageSessionId());
      return;
    }
    if (recording && closest(target, "#composer-submit")) {
      event.preventDefault();
      event.stopPropagation();
      void end();
    }
  }, true);

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form || form.id !== "composer") return;
    if (!recording) return;
    event.preventDefault();
    event.stopPropagation();
    void end();
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.code === "AltRight") {
      event.preventDefault();
      if (recording) void end();
      else void start("");
      return;
    }
    if (event.key === "Delete" && recording) {
      event.preventDefault();
      void cancel();
    }
  });

  const afterSwap = () => {
    paint();
    const focused = document.querySelector("#prompt");
    if (focused && focused === document.activeElement) noteFocus();
  };
  for (const eventName of ["htmx:afterSwap", "htmx:afterSettle", "htmx:sseMessage"]) {
    document.addEventListener(eventName, afterSwap);
  }

  const poll = async () => {
    try {
      const status = await readStatus();
      if (status.state === "recording" && !recording) setRecording(true);
      if (status.state !== "recording" && recording && !capture) setRecording(false);
    } catch {}
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", afterSwap, { once: true });
  } else {
    afterSwap();
  }
  void poll();
  pollTimer = window.setInterval(() => { void poll(); }, 1000);
  window.addEventListener("pagehide", () => clearInterval(pollTimer), { once: true });
})();
