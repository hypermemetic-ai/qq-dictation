// In-process dictation service. Bind is frozen at start; end recognizes and
// autosubmits on that session; cancel drops. One live recording at a time.

import { readFileSync } from "node:fs";
import { join } from "node:path";

export const SESSION_ID = /^session-[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class DictationError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

/** Ordinary user speech. Leading slashes must not become T-71 command lines. */
export function asUserSpeech(text) {
  const raw = String(text ?? "").replace(/^\uFEFF/, "").trim();
  if (!raw) return "";
  return raw.replace(/^[\\/]+/, "").trim();
}

export function parseSessionId(value) {
  const id = String(value ?? "").trim();
  return SESSION_ID.test(id) ? id : "";
}

export function resumeSessionId(env = process.env) {
  const home = String(env.DSH_HOME ?? "").trim();
  if (!home.startsWith("/")) return "";
  for (const name of ["qq.session", "qq-console.session"]) {
    try {
      const id = parseSessionId(readFileSync(join(home, name), "utf8"));
      if (id) return id;
    } catch {
      // Missing or unreadable resume file is not an error.
    }
  }
  return "";
}

export function createDictationService(ctx, config = {}) {
  const qq = ctx.get?.("qq", false) ?? ctx.get?.("qq") ?? null;
  if (!qq || typeof qq.prompt !== "function") {
    throw new Error("qq-dictation: qq service is unavailable");
  }

  const recognize = typeof config.recognize === "function"
    ? config.recognize
    : async () => {
        throw new DictationError("qq-dictation: recognizer is not configured", 503);
      };
  const now = typeof config.now === "function" ? config.now : () => Date.now();
  const env = config.env ?? process.env;

  let lastFocus = "";
  let live = null;

  function snapshot() {
    return Object.freeze({
      state: live ? "recording" : "idle",
      boundSessionId: live?.sessionId ?? null,
      lastFocus: lastFocus || null,
    });
  }

  function noteFocus(sessionId) {
    const id = parseSessionId(sessionId);
    if (id) lastFocus = id;
    return snapshot();
  }

  function resolveStartBind(requested) {
    const explicit = parseSessionId(requested);
    if (explicit) return explicit;
    if (lastFocus) return lastFocus;
    const resume = resumeSessionId(env);
    if (resume) return resume;
    const fallback = parseSessionId(qq.defaultSessionId);
    if (fallback) return fallback;
    throw new DictationError("qq-dictation: no session to bind", 409);
  }

  async function sessionExists(sessionId) {
    if (typeof qq.read === "function") {
      try {
        await qq.read(sessionId);
        return true;
      } catch (error) {
        if (Number(error?.status) === 404) return false;
        throw error;
      }
    }
    if (typeof qq.list === "function") {
      const rows = await qq.list();
      return Array.isArray(rows) && rows.some((row) => row?.id === sessionId);
    }
    return true;
  }

  async function start({ sessionId } = {}) {
    if (live) throw new DictationError("qq-dictation: already recording", 409);
    live = {
      sessionId: resolveStartBind(sessionId),
      startedAt: now(),
      chunks: [],
    };
    return snapshot();
  }

  function appendAudio(audio) {
    if (!live) throw new DictationError("qq-dictation: not recording", 409);
    const bytes = Buffer.isBuffer(audio) ? audio : Buffer.from(audio ?? []);
    if (bytes.length) live.chunks.push(bytes);
    return snapshot();
  }

  async function cancel() {
    live = null;
    return snapshot();
  }

  async function end({ audio, text } = {}) {
    const bound = live;
    live = null;
    if (!bound) throw new DictationError("qq-dictation: not recording", 409);

    const incoming = audio == null ? Buffer.alloc(0) : Buffer.isBuffer(audio) ? audio : Buffer.from(audio);
    const buffered = bound.chunks.length ? Buffer.concat(bound.chunks) : Buffer.alloc(0);
    const payload = incoming.length ? incoming : buffered;

    let recognized = asUserSpeech(text);
    if (!recognized && payload.length) {
      recognized = asUserSpeech(await recognize(payload, { sessionId: bound.sessionId }));
    }
    if (!recognized) {
      return Object.freeze({ ...snapshot(), sent: false, reason: "empty" });
    }
    if (!(await sessionExists(bound.sessionId))) {
      return Object.freeze({
        ...snapshot(),
        sent: false,
        reason: "gone",
        boundSessionId: bound.sessionId,
        message: "Bound session is gone; dictation dropped.",
      });
    }
    try {
      await qq.prompt(bound.sessionId, recognized);
    } catch (error) {
      if (Number(error?.status) === 404) {
        return Object.freeze({
          ...snapshot(),
          sent: false,
          reason: "gone",
          boundSessionId: bound.sessionId,
          message: "Bound session is gone; dictation dropped.",
        });
      }
      throw error;
    }
    return Object.freeze({
      ...snapshot(),
      sent: true,
      boundSessionId: bound.sessionId,
      text: recognized,
    });
  }

  return Object.freeze({
    snapshot,
    noteFocus,
    lastFocus: () => lastFocus || null,
    resumeSessionId: () => resumeSessionId(env),
    start,
    appendAudio,
    end,
    cancel,
  });
}
