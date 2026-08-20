// In-process dictation service. Bind is frozen at start; end recognizes and
// autosubmits on that session; cancel drops. One owned browser capture at a
// time. Capture leases survive a Cordis fiber replacement but expire without
// an owner heartbeat.

import { readFileSync } from "node:fs";
import { join } from "node:path";

export const SESSION_ID = /^session-[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const CAPTURE_LEASE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const DEFAULT_CAPTURE_LEASE_MS = 30_000;

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

export function parseCaptureLeaseId(value) {
  const id = String(value ?? "").trim();
  return CAPTURE_LEASE_ID.test(id) ? id.toLowerCase() : "";
}

export function resumeSessionId(env = process.env) {
  const home = String(env.DSH_HOME ?? "").trim();
  if (!home.startsWith("/")) return "";
  try {
    return parseSessionId(readFileSync(join(home, "qq.session"), "utf8"));
  } catch {
    // Missing or unreadable resume file is not an error.
  }
  return "";
}

export function createCaptureLeaseAuthority(options = {}) {
  const now = typeof options.now === "function" ? options.now : () => Date.now();
  const ttlMs = Number.isFinite(options.ttlMs) && options.ttlMs > 0
    ? Number(options.ttlMs)
    : DEFAULT_CAPTURE_LEASE_MS;
  const records = new Map();
  let activeId = "";

  function expire() {
    if (!activeId) return;
    const active = records.get(activeId);
    if (!active || active.state !== "active") {
      activeId = "";
      return;
    }
    if (now() - active.touchedAt < ttlMs) return;
    active.state = "expired";
    active.touchedAt = now();
    activeId = "";
  }

  function requireId(value) {
    const id = parseCaptureLeaseId(value);
    if (!id) throw new DictationError("qq-dictation: a capture lease is required", 400);
    return id;
  }

  function claim(value, sessionId) {
    const id = requireId(value);
    expire();
    const previous = records.get(id);
    if (previous?.state === "active") {
      throw new DictationError("qq-dictation: capture lease requires resume", 409);
    }
    if (previous) {
      throw new DictationError("qq-dictation: capture lease is no longer valid", 409);
    }
    if (activeId) {
      throw new DictationError("qq-dictation: another browser owns dictation", 409);
    }
    const record = {
      id,
      sessionId,
      state: "active",
      touchedAt: now(),
    };
    records.set(id, record);
    activeId = id;
    return Object.freeze({ id, sessionId });
  }

  function resume(value) {
    const id = requireId(value);
    expire();
    const record = records.get(id);
    if (!record || record.state !== "active" || activeId !== id) {
      throw new DictationError("qq-dictation: capture lease cannot resume", 409);
    }
    record.touchedAt = now();
    return Object.freeze({ id, sessionId: record.sessionId });
  }

  function revoke(value, reason = "cancelled") {
    const id = requireId(value);
    expire();
    if (activeId && activeId !== id) {
      throw new DictationError("qq-dictation: capture belongs to another browser", 409);
    }
    const record = records.get(id);
    if (!record) throw new DictationError("qq-dictation: capture lease is unknown", 409);
    if (record.state !== "active") return false;
    record.state = reason;
    record.touchedAt = now();
    activeId = "";
    return true;
  }

  function view(value, { renew = false } = {}) {
    expire();
    const id = parseCaptureLeaseId(value);
    const active = activeId ? records.get(activeId) : null;
    const ownership = active ? (id && id === active.id ? "local" : "foreign") : null;
    if (renew && ownership === "local") active.touchedAt = now();
    return Object.freeze({
      ownership,
      activeId: active?.id ?? "",
      sessionId: active?.sessionId ?? "",
      resumable: ownership === "local",
      revoked: Boolean(id && records.has(id) && records.get(id).state !== "active"),
    });
  }

  function isActive(value) {
    const id = parseCaptureLeaseId(value);
    if (!id) return false;
    expire();
    return activeId === id && records.get(id)?.state === "active";
  }

  return Object.freeze({
    ttlMs,
    claim,
    resume,
    revoke,
    view,
    isActive,
  });
}

// Cordis HMR may reevaluate this module while a browser continues capturing.
// A process-global symbol keeps only the lease authority (never audio) alive
// across that fiber/module replacement. qq runs one loopback host per process.
const DEFAULT_AUTHORITY_KEY = Symbol.for("@hypermemetic-ai/qq-dictation/capture-lease-authority");
if (!globalThis[DEFAULT_AUTHORITY_KEY]) {
  Object.defineProperty(globalThis, DEFAULT_AUTHORITY_KEY, {
    value: createCaptureLeaseAuthority(),
    enumerable: false,
    configurable: false,
    writable: false,
  });
}
export const defaultCaptureLeaseAuthority = globalThis[DEFAULT_AUTHORITY_KEY];

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
  const leases = config.leaseAuthority ?? createCaptureLeaseAuthority({
    now,
    ttlMs: config.captureLeaseMs,
  });

  let lastFocus = "";
  let live = null;

  function syncLiveLease() {
    if (live && !leases.isActive(live.leaseId)) live = null;
  }

  function snapshot({ leaseId, renew = false } = {}) {
    syncLiveLease();
    const lease = leases.view(leaseId, { renew });
    const local = lease.ownership === "local";
    return Object.freeze({
      state: live ? "recording" : "idle",
      capture: lease.ownership,
      resumable: Boolean(!live && local && lease.resumable),
      boundSessionId: local ? (live?.sessionId ?? lease.sessionId ?? null) : null,
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

  async function start({ sessionId, leaseId } = {}) {
    const id = parseCaptureLeaseId(leaseId);
    if (!id) throw new DictationError("qq-dictation: a capture lease is required", 400);
    syncLiveLease();
    if (live) {
      if (live.leaseId === id) return snapshot({ leaseId: id, renew: true });
      throw new DictationError("qq-dictation: another browser owns dictation", 409);
    }
    const bound = resolveStartBind(sessionId);
    leases.claim(id, bound);
    live = {
      leaseId: id,
      sessionId: bound,
      startedAt: now(),
      chunks: [],
    };
    return snapshot({ leaseId: id, renew: true });
  }

  async function resume({ leaseId } = {}) {
    syncLiveLease();
    if (live) throw new DictationError("qq-dictation: already recording", 409);
    const lease = leases.resume(leaseId);
    live = {
      leaseId: lease.id,
      sessionId: lease.sessionId,
      startedAt: now(),
      chunks: [],
    };
    return snapshot({ leaseId: lease.id, renew: true });
  }

  function assertOwner(leaseId) {
    const id = parseCaptureLeaseId(leaseId);
    syncLiveLease();
    if (!id) throw new DictationError("qq-dictation: a capture lease is required", 400);
    if (!live) throw new DictationError("qq-dictation: not recording", 409);
    if (live.leaseId !== id) {
      throw new DictationError("qq-dictation: capture belongs to another browser", 409);
    }
    return id;
  }

  function appendAudio(audio, { leaseId } = {}) {
    assertOwner(leaseId);
    const bytes = Buffer.isBuffer(audio) ? audio : Buffer.from(audio ?? []);
    if (bytes.length) live.chunks.push(bytes);
    return snapshot({ leaseId, renew: true });
  }

  async function cancel({ leaseId } = {}) {
    const id = parseCaptureLeaseId(leaseId);
    if (!id) throw new DictationError("qq-dictation: a capture lease is required", 400);
    syncLiveLease();
    if (live && live.leaseId !== id) {
      throw new DictationError("qq-dictation: capture belongs to another browser", 409);
    }
    leases.revoke(id, "cancelled");
    if (live?.leaseId === id) live = null;
    return snapshot({ leaseId: id });
  }

  // Service/fiber disposal drops only in-memory audio. The browser lease stays
  // active for an explicit owner end -> resume operation, or until heartbeat
  // expiry. It is not a user cancellation.
  async function release() {
    live = null;
    return snapshot();
  }

  async function end({ audio, text, leaseId } = {}) {
    const id = assertOwner(leaseId);
    const bound = live;
    live = null;
    leases.revoke(id, "completed");

    const incoming = audio == null ? Buffer.alloc(0) : Buffer.isBuffer(audio) ? audio : Buffer.from(audio);
    const buffered = bound.chunks.length ? Buffer.concat(bound.chunks) : Buffer.alloc(0);
    const payload = incoming.length ? incoming : buffered;

    let recognized = asUserSpeech(text);
    if (!recognized && payload.length) {
      recognized = asUserSpeech(await recognize(payload, { sessionId: bound.sessionId }));
    }
    if (!recognized) {
      return Object.freeze({ ...snapshot({ leaseId: id }), sent: false, reason: "empty" });
    }
    if (!(await sessionExists(bound.sessionId))) {
      return Object.freeze({
        ...snapshot({ leaseId: id }),
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
          ...snapshot({ leaseId: id }),
          sent: false,
          reason: "gone",
          boundSessionId: bound.sessionId,
          message: "Bound session is gone; dictation dropped.",
        });
      }
      throw error;
    }
    return Object.freeze({
      ...snapshot({ leaseId: id }),
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
    resume,
    appendAudio,
    end,
    cancel,
    release,
  });
}
