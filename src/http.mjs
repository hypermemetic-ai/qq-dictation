// Own HTTP under /qq/dictate. Longest-prefix wins over qq-ui's /qq mount.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { DictationError } from "./service.mjs";

const MAX_BODY_BYTES = 8_388_608;
const CLIENT_PATH = fileURLToPath(new URL("./client.js", import.meta.url));
const LEGACY_DIRECT_PROTOCOL = "legacy-direct-v1";
const COMPOSER_HANDOFF_PROTOCOL = "composer-handoff-v1";
const SERVER_DELIVERY_PROTOCOL = "server-delivery-v1";
const MULTIPART_HEADER_BYTES = 8_192;

const SECURITY_HEADERS = Object.freeze({
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
});

function write(res, status, headers, body) {
  res.writeHead(status, { ...SECURITY_HEADERS, ...headers });
  res.end(body);
}

function json(res, status, value) {
  write(res, status, { "Content-Type": "application/json; charset=utf-8" }, `${JSON.stringify(value)}\n`);
}

function text(res, status, message) {
  write(res, status, { "Content-Type": "text/plain; charset=utf-8" }, `${message}\n`);
}

function sameOrigin(req) {
  const site = req.headers["sec-fetch-site"];
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = req.headers.origin;
  if (!origin || origin === "null") return !site || site === "same-origin" || site === "none";
  try {
    return new URL(origin).host === req.headers.host;
  } catch {
    return false;
  }
}

async function readBody(req, limit = MAX_BODY_BYTES) {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of req) {
    bytes += chunk.length;
    if (bytes > limit) {
      throw new DictationError("qq-dictation: body too large", 413);
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

async function readJson(req) {
  const raw = await readBody(req, 65_536);
  if (raw.length === 0) return {};
  try {
    const parsed = JSON.parse(raw.toString("utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    throw new DictationError("qq-dictation: expected JSON", 415);
  }
}

function multipartBoundary(contentType) {
  const match = String(contentType ?? "").match(/(?:^|;)\s*boundary=(?:"([^"]+)"|([^;\s]+))/i);
  const boundary = match?.[1] ?? match?.[2] ?? "";
  if (!boundary || boundary.length > 70 || /[\r\n]/.test(boundary)) {
    throw new DictationError("qq-dictation: invalid multipart boundary", 415);
  }
  return boundary;
}

function multipartName(headerText) {
  for (const line of headerText.split("\r\n")) {
    const separator = line.indexOf(":");
    if (separator < 0 || line.slice(0, separator).trim().toLowerCase() !== "content-disposition") continue;
    const match = line.slice(separator + 1).match(/(?:^|;)\s*name="([^"]+)"/i);
    if (match) return match[1];
  }
  return "";
}

function parseEndMultipart(raw, contentType) {
  const boundary = multipartBoundary(contentType);
  const delimiter = Buffer.from(`--${boundary}`);
  const nextDelimiter = Buffer.from(`\r\n--${boundary}`);
  const headerSeparator = Buffer.from("\r\n\r\n");
  let cursor = 0;
  let draft = "";
  let audio = null;

  if (!raw.subarray(0, delimiter.length).equals(delimiter)) {
    throw new DictationError("qq-dictation: malformed multipart body", 415);
  }

  while (cursor < raw.length) {
    if (!raw.subarray(cursor, cursor + delimiter.length).equals(delimiter)) {
      throw new DictationError("qq-dictation: malformed multipart delimiter", 415);
    }
    cursor += delimiter.length;
    if (raw.subarray(cursor, cursor + 2).toString("ascii") === "--") {
      cursor += 2;
      if (cursor === raw.length || raw.subarray(cursor).toString("ascii") === "\r\n") break;
      throw new DictationError("qq-dictation: malformed multipart ending", 415);
    }
    if (raw.subarray(cursor, cursor + 2).toString("ascii") !== "\r\n") {
      throw new DictationError("qq-dictation: malformed multipart part", 415);
    }
    cursor += 2;

    const headerEnd = raw.indexOf(headerSeparator, cursor);
    if (headerEnd < 0 || headerEnd - cursor > MULTIPART_HEADER_BYTES) {
      throw new DictationError("qq-dictation: malformed multipart headers", 415);
    }
    const name = multipartName(raw.subarray(cursor, headerEnd).toString("latin1"));
    const valueStart = headerEnd + headerSeparator.length;
    const valueEnd = raw.indexOf(nextDelimiter, valueStart);
    if (valueEnd < 0) throw new DictationError("qq-dictation: unterminated multipart part", 415);
    const value = raw.subarray(valueStart, valueEnd);
    if (name === "draft") draft = value.toString("utf8");
    if (name === "audio") audio = Buffer.from(value);
    cursor = valueEnd + 2;
  }

  if (audio === null) throw new DictationError("qq-dictation: multipart audio is required", 400);
  return Object.freeze({ audio, draft });
}

async function readEndMultipart(req, contentType) {
  return parseEndMultipart(await readBody(req), contentType);
}

function routeOf(basePath, pathname) {
  if (pathname === basePath || pathname === `${basePath}/`) return "status";
  if (pathname === `${basePath}/client.js`) return "client";
  if (pathname === `${basePath}/focus`) return "focus";
  if (pathname === `${basePath}/start`) return "start";
  if (pathname === `${basePath}/resume`) return "resume";
  if (pathname === `${basePath}/chunk`) return "chunk";
  if (pathname === `${basePath}/end`) return "end";
  if (pathname === `${basePath}/cancel`) return "cancel";
  return "";
}

function requestLease(req) {
  return String(req.headers["x-qq-dictation-lease"] ?? "").trim();
}

function requestProtocol(req) {
  const requested = String(req.headers["x-qq-dictation-protocol"] ?? "").trim();
  if (requested === COMPOSER_HANDOFF_PROTOCOL) return COMPOSER_HANDOFF_PROTOCOL;
  if (requested === SERVER_DELIVERY_PROTOCOL) return SERVER_DELIVERY_PROTOCOL;
  return LEGACY_DIRECT_PROTOCOL;
}

async function endResponse(service, result, protocol, draft = "") {
  if (protocol === COMPOSER_HANDOFF_PROTOCOL) return result;
  return service.submitRecognition(result, {
    draft: protocol === SERVER_DELIVERY_PROTOCOL ? draft : "",
  });
}

export const internals = Object.freeze({
  MAX_BODY_BYTES,
  SECURITY_HEADERS,
  routeOf,
  sameOrigin,
  requestLease,
  requestProtocol,
  multipartBoundary,
  parseEndMultipart,
  LEGACY_DIRECT_PROTOCOL,
  COMPOSER_HANDOFF_PROTOCOL,
  SERVER_DELIVERY_PROTOCOL,
});

export function createDictateHandler(service, options = {}) {
  const basePath = String(options.basePath ?? "/qq/dictate");
  const clientBody = options.clientBody ?? readFileSync(CLIENT_PATH);

  return async function dictateHandler(req, res) {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const route = routeOf(basePath, url.pathname);

    if (route === "client") {
      if (req.method !== "GET" && req.method !== "HEAD") {
        write(res, 405, { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=utf-8" }, "Method not allowed\n");
        return;
      }
      write(
        res,
        200,
        { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store" },
        req.method === "HEAD" ? undefined : clientBody,
      );
      return;
    }

    if (route === "status") {
      if (req.method !== "GET") {
        write(res, 405, { Allow: "GET", "Content-Type": "text/plain; charset=utf-8" }, "Method not allowed\n");
        return;
      }
      json(res, 200, service.snapshot({ leaseId: requestLease(req), renew: true }));
      return;
    }

    if (!route) {
      text(res, 404, "Not found");
      return;
    }

    if (req.method !== "POST") {
      write(res, 405, { Allow: "POST", "Content-Type": "text/plain; charset=utf-8" }, "Method not allowed\n");
      return;
    }
    if (!sameOrigin(req)) {
      text(res, 403, "Cross-origin dictation refused");
      return;
    }

    try {
      if (route === "focus") {
        const body = await readJson(req);
        json(res, 200, service.noteFocus(body.sessionId));
        return;
      }
      if (route === "start") {
        const body = await readJson(req);
        json(res, 200, await service.start({
          sessionId: body.sessionId,
          leaseId: body.leaseId,
        }));
        return;
      }
      if (route === "resume") {
        const body = await readJson(req);
        json(res, 200, await service.resume({ leaseId: body.leaseId }));
        return;
      }
      if (route === "chunk") {
        const audio = await readBody(req);
        json(res, 200, service.appendAudio(audio, { leaseId: requestLease(req) }));
        return;
      }
      if (route === "cancel") {
        const body = await readJson(req);
        json(res, 200, await service.cancel({ leaseId: body.leaseId }));
        return;
      }
      if (route === "end") {
        const protocol = requestProtocol(req);
        const contentType = String(req.headers["content-type"] ?? "");
        const type = contentType.split(";", 1)[0].trim().toLowerCase();
        if (type === "application/json") {
          const body = await readJson(req);
          const result = await service.end({
            text: body.text,
            leaseId: body.leaseId,
          });
          json(res, 200, await endResponse(service, result, protocol, body.draft));
          return;
        }
        if (type === "multipart/form-data") {
          const body = await readEndMultipart(req, contentType);
          const result = await service.end({
            audio: body.audio,
            leaseId: requestLease(req),
          });
          json(res, 200, await endResponse(service, result, protocol, body.draft));
          return;
        }
        const audio = await readBody(req);
        const result = await service.end({ audio, leaseId: requestLease(req) });
        json(res, 200, await endResponse(service, result, protocol));
      }
    } catch (error) {
      const status = Number.isInteger(error?.status) ? error.status : 500;
      json(res, status, {
        error: error instanceof Error ? error.message : String(error),
        recognized: false,
      });
    }
  };
}
