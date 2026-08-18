// Own HTTP under /qq/dictate. Longest-prefix wins over qq-ui's /qq mount.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { DictationError } from "./service.mjs";

const MAX_BODY_BYTES = 8_388_608;
const CLIENT_PATH = fileURLToPath(new URL("./client.js", import.meta.url));

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

function routeOf(basePath, pathname) {
  if (pathname === basePath || pathname === `${basePath}/`) return "status";
  if (pathname === `${basePath}/client.js`) return "client";
  if (pathname === `${basePath}/focus`) return "focus";
  if (pathname === `${basePath}/start`) return "start";
  if (pathname === `${basePath}/chunk`) return "chunk";
  if (pathname === `${basePath}/end`) return "end";
  if (pathname === `${basePath}/cancel`) return "cancel";
  return "";
}

export const internals = Object.freeze({
  MAX_BODY_BYTES,
  SECURITY_HEADERS,
  routeOf,
  sameOrigin,
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
      json(res, 200, service.snapshot());
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
        json(res, 200, await service.start({ sessionId: body.sessionId }));
        return;
      }
      if (route === "chunk") {
        const audio = await readBody(req);
        json(res, 200, service.appendAudio(audio));
        return;
      }
      if (route === "cancel") {
        await readBody(req, 4096);
        json(res, 200, await service.cancel());
        return;
      }
      if (route === "end") {
        const type = String(req.headers["content-type"] ?? "").split(";", 1)[0].trim();
        if (type === "application/json") {
          const body = await readJson(req);
          json(res, 200, await service.end({ text: body.text }));
          return;
        }
        const audio = await readBody(req);
        json(res, 200, await service.end({ audio }));
      }
    } catch (error) {
      const status = Number.isInteger(error?.status) ? error.status : 500;
      json(res, status, {
        error: error instanceof Error ? error.message : String(error),
        sent: false,
      });
    }
  };
}
