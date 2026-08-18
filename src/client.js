(() => {
  "use strict";

  const PREFIX = "/qq/dictate";
  const MIC = "Mic";
  const CANCEL = "X";
  let recorder = null;
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
    button.textContent = recording ? CANCEL : MIC;
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

  const stopTracks = () => {
    try {
      recorder?.stream?.getTracks?.().forEach((track) => track.stop());
    } catch {}
    try { if (recorder && recorder.state !== "inactive") recorder.stop(); } catch {}
    recorder = null;
  };

  const setRecording = (next) => {
    recording = next;
    paint();
  };

  const startMic = async () => {
    if (!navigator.mediaDevices?.getUserMedia) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "";
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    recorder.addEventListener("dataavailable", (event) => {
      if (!event.data || !event.data.size) return;
      void fetch(`${PREFIX}/chunk`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": event.data.type || "application/octet-stream" },
        body: event.data,
      }).catch(() => {});
    });
    recorder.start(250);
  };

  const start = async (bindSessionId) => {
    if (recording || starting) return;
    starting = true;
    try {
      const status = await readStatus();
      if (status.state === "recording") {
        setRecording(true);
        return;
      }
      const sessionId = bindSessionId || "";
      await postJson("/start", sessionId ? { sessionId } : {});
      try {
        await startMic();
      } catch {
        await postJson("/cancel", {});
        setRecording(false);
        return;
      }
      setRecording(true);
    } catch {
      stopTracks();
      setRecording(false);
    } finally {
      starting = false;
    }
  };

  const end = async () => {
    const status = recording ? { state: "recording" } : await readStatus();
    if (status.state !== "recording" && !recording) return;
    setRecording(false);
    stopTracks();
    try {
      await fetch(`${PREFIX}/end`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/octet-stream" },
        body: new Blob(),
      });
    } catch {
      try { await postJson("/cancel", {}); } catch {}
    }
  };

  const cancel = async () => {
    setRecording(false);
    stopTracks();
    try { await postJson("/cancel", {}); } catch {}
  };

  document.addEventListener("focusin", (event) => {
    if (event.target instanceof HTMLTextAreaElement && event.target.id === "prompt") {
      noteFocus();
    }
  });

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const dictate = target?.closest("#composer-dictate");
    if (dictate) {
      event.preventDefault();
      if (recording) void cancel();
      else void start(pageSessionId());
      return;
    }
    if (recording && target?.closest("#composer-submit")) {
      event.preventDefault();
      event.stopPropagation();
      void end();
    }
  }, true);

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.id !== "composer") return;
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
    if (document.querySelector("#prompt") === document.activeElement) noteFocus();
  };
  for (const eventName of ["htmx:afterSwap", "htmx:afterSettle", "htmx:sseMessage"]) {
    document.addEventListener(eventName, afterSwap);
  }

  const poll = async () => {
    try {
      const status = await readStatus();
      if (status.state === "recording" && !recording) setRecording(true);
      if (status.state !== "recording" && recording && !recorder) setRecording(false);
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
