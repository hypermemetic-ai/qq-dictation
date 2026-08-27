import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

import {
  createCaptureLeaseAuthority,
  createDictationService,
  mergeComposerText,
} from "../src/service.mjs";
import { createDictateHandler, internals } from "../src/http.mjs";

const sessionId = (marker) => `session-63a11000-0000-4000-8000-${String(marker).padStart(12, "0")}`;
const leaseId = (marker) => `73a11000-0000-4000-8000-${String(marker).padStart(12, "0")}`;
const A = sessionId("a");
const B = sessionId("b");
const LEASE_A = leaseId("a");

function fakeQq(options = {}) {
  const present = new Set(options.sessions ?? [A, B]);
  const prompts = [];
  return {
    defaultSessionId: A,
    prompts,
    present,
    async read(id) {
      if (!present.has(id)) {
        const error = new Error("not found");
        error.status = 404;
        throw error;
      }
      return { id };
    },
    async list() {
      return [...present].map((id) => ({ id }));
    },
    async prompt(id, text) {
      if (!present.has(id)) {
        const error = new Error("not found");
        error.status = 404;
        throw error;
      }
      prompts.push({ id, text });
    },
  };
}

function serviceFor(qq, recognized = "spoken words") {
  return createDictationService({ get: () => qq }, {
    leaseAuthority: createCaptureLeaseAuthority(),
    recognize: async () => recognized,
  });
}


test("server delivery merges the frozen draft and speech for the frozen session", async () => {
  assert.equal(mergeComposerText("draft", " spoken words "), "draft spoken words");
  assert.equal(mergeComposerText("draft\n", "/spoken words"), "draft\nspoken words");
  assert.equal(mergeComposerText("   ", "spoken words"), "spoken words");

  const qq = fakeQq();
  const service = serviceFor(qq);
  await service.start({ sessionId: A, leaseId: LEASE_A });
  service.noteFocus(B);
  const result = await service.end({ leaseId: LEASE_A, text: "spoken words" });
  const sent = await service.submitRecognition(result, { draft: "A draft" });

  assert.equal(sent.sent, true);
  assert.equal(sent.boundSessionId, A);
  assert.equal(sent.text, "A draft spoken words");
  assert.deepEqual(qq.prompts, [{ id: A, text: "A draft spoken words" }]);
});

test("HMR resume retains A's frozen binding and server-delivers its draft", async () => {
  const qq = fakeQq();
  const leases = createCaptureLeaseAuthority({ ttlMs: 10_000 });
  const beforeReload = createDictationService({ get: () => qq }, {
    leaseAuthority: leases,
    recognize: async () => "old recognizer must not run",
  });
  await beforeReload.start({ sessionId: A, leaseId: LEASE_A });
  await beforeReload.release();

  const afterReload = createDictationService({ get: () => qq }, {
    leaseAuthority: leases,
    recognize: async () => "resumed speech",
  });
  assert.equal(afterReload.snapshot({ leaseId: LEASE_A }).boundSessionId, A);
  await afterReload.resume({ leaseId: LEASE_A });
  afterReload.noteFocus(B);
  const result = await afterReload.end({ leaseId: LEASE_A, audio: Buffer.from("wav") });
  const sent = await afterReload.submitRecognition(result, { draft: "A draft" });

  assert.equal(sent.sent, true);
  assert.deepEqual(qq.prompts, [{ id: A, text: "A draft resumed speech" }]);
});

test("empty recognition and a deleted frozen target send nothing", async () => {
  const emptyQq = fakeQq();
  const emptyService = serviceFor(emptyQq, "");
  await emptyService.start({ sessionId: A, leaseId: LEASE_A });
  const empty = await emptyService.end({ leaseId: LEASE_A, audio: Buffer.from("wav") });
  const emptySent = await emptyService.submitRecognition(empty, { draft: "keep draft" });
  assert.equal(emptySent.sent, false);
  assert.deepEqual(emptyQq.prompts, []);

  const goneQq = fakeQq({ sessions: [A, B] });
  const goneService = serviceFor(goneQq);
  await goneService.start({ sessionId: A, leaseId: LEASE_A });
  goneQq.present.delete(A);
  const gone = await goneService.end({ leaseId: LEASE_A, text: "speech" });
  const goneSent = await goneService.submitRecognition(gone, { draft: "must not send" });
  assert.equal(goneSent.sent, false);
  assert.equal(goneSent.reason, "gone");
  assert.deepEqual(goneQq.prompts, []);

  const cancelledQq = fakeQq();
  const cancelledService = serviceFor(cancelledQq);
  await cancelledService.start({ sessionId: A, leaseId: LEASE_A });
  await cancelledService.cancel({ leaseId: LEASE_A });
  await assert.rejects(
    () => cancelledService.end({ leaseId: LEASE_A, text: "must not send" }),
    /not recording/,
  );
  assert.deepEqual(cancelledQq.prompts, []);

  const foreignQq = fakeQq();
  const foreignService = serviceFor(foreignQq);
  await foreignService.start({ sessionId: A, leaseId: LEASE_A });
  await assert.rejects(
    () => foreignService.end({ leaseId: leaseId("b"), text: "must not send" }),
    /another browser/,
  );
  assert.deepEqual(foreignQq.prompts, []);
  await foreignService.cancel({ leaseId: LEASE_A });
});

function request(server, path, options = {}) {
  const address = server.address();
  return new Promise((resolve, reject) => {
    const req = httpRequest({
      host: "127.0.0.1",
      port: address.port,
      path,
      method: options.method ?? "GET",
      agent: false,
      headers: options.headers,
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({
        status: res.statusCode,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    req.on("error", reject);
    if (options.body != null) req.write(options.body);
    req.end();
  });
}

async function withServer(handler, run) {
  const server = createServer((req, res) => void handler(req, res));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await run(server);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
}

async function startHttp(server, lease = LEASE_A) {
  const response = await request(server, "/qq/dictate/start", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({ sessionId: A, leaseId: lease }),
  });
  assert.equal(response.status, 200, response.body);
}

async function multipartBody(draft, audio = Buffer.from("fake wav")) {
  const form = new FormData();
  form.append("draft", new Blob([draft], { type: "text/plain;charset=utf-8" }), "composer.txt");
  form.append("audio", new Blob([audio], { type: "audio/wav" }), "dictation.wav");
  const encoded = new Request("http://localhost/", { method: "POST", body: form });
  return {
    contentType: encoded.headers.get("content-type"),
    body: Buffer.from(await encoded.arrayBuffer()),
  };
}

test("new HTTP protocol server-delivers multipart draft while composer v1 remains recognize-only", async () => {
  assert.equal(internals.SERVER_DELIVERY_PROTOCOL, "server-delivery-v1");
  const qq = fakeQq();
  const service = serviceFor(qq, "speech");
  const handler = createDictateHandler(service);

  await withServer(handler, async (server) => {
    await startHttp(server);
    const multipart = await multipartBody("frozen\ndraft");
    const delivered = await request(server, "/qq/dictate/end", {
      method: "POST",
      headers: {
        "content-type": multipart.contentType,
        "sec-fetch-site": "same-origin",
        "x-qq-dictation-lease": LEASE_A,
        "x-qq-dictation-protocol": "server-delivery-v1",
      },
      body: multipart.body,
    });
    assert.equal(delivered.status, 200, delivered.body);
    assert.equal(JSON.parse(delivered.body).sent, true);
    assert.deepEqual(qq.prompts, [{ id: A, text: "frozen\ndraft speech" }]);

    const secondLease = leaseId("d");
    await startHttp(server, secondLease);
    const recognized = await request(server, "/qq/dictate/end", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        "x-qq-dictation-protocol": "composer-handoff-v1",
      },
      body: JSON.stringify({ leaseId: secondLease, text: "old client speech", draft: "must not send" }),
    });
    assert.equal(recognized.status, 200, recognized.body);
    assert.equal(JSON.parse(recognized.body).recognized, true);
    assert.deepEqual(qq.prompts, [{ id: A, text: "frozen\ndraft speech" }]);

    const emptyDraftLease = leaseId("e");
    await startHttp(server, emptyDraftLease);
    const speechOnly = await request(server, "/qq/dictate/end", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "sec-fetch-site": "same-origin",
        "x-qq-dictation-protocol": "server-delivery-v1",
      },
      body: JSON.stringify({ leaseId: emptyDraftLease, text: "/speech only", draft: "   " }),
    });
    assert.equal(speechOnly.status, 200, speechOnly.body);
    assert.equal(JSON.parse(speechOnly.body).text, "speech only");

    const legacyLease = leaseId("f");
    await startHttp(server, legacyLease);
    const legacy = await request(server, "/qq/dictate/end", {
      method: "POST",
      headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
      body: JSON.stringify({ leaseId: legacyLease, text: "legacy speech", draft: "ignored draft" }),
    });
    assert.equal(legacy.status, 200, legacy.body);
    assert.equal(JSON.parse(legacy.body).sent, true);
    assert.deepEqual(qq.prompts, [
      { id: A, text: "frozen\ndraft speech" },
      { id: A, text: "speech only" },
      { id: A, text: "legacy speech" },
    ]);
  });
});

class Element {
  constructor(id = "") {
    this.id = id;
    this.dataset = {};
    this.attributes = {};
    this.parentElement = null;
    this.hidden = false;
    this.disabled = false;
    const names = new Set();
    this.classList = {
      toggle(name, force) { force ? names.add(name) : names.delete(name); },
      contains(name) { return names.has(name); },
    };
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  replaceChildren(value = "") { this.textContent = String(value); }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) {
      if (selector === `#${node.id}`) return node;
    }
    return null;
  }
}
class HTMLFormElement extends Element {}
class HTMLTextAreaElement extends Element {
  constructor(id) { super(id); this.value = ""; this.required = true; }
  setSelectionRange() {}
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function clientHarness(options = {}) {
  const source = readFileSync(new URL("../src/client.js", import.meta.url), "utf8");
  const documentListeners = [];
  const windowListeners = [];
  const fetches = [];
  const storage = new Map(options.storage ?? []);
  let current = options.session ?? A;
  let leaseSerial = 1;
  let endGate = options.deferEnd ? deferred() : null;
  let processor;

  const installComposer = (session, draft = "") => {
    current = session;
    const composer = new HTMLFormElement("composer");
    composer.dataset.sessionId = session;
    const prompt = new HTMLTextAreaElement("prompt");
    prompt.value = draft;
    prompt.form = composer;
    prompt.parentElement = composer;
    const dictate = new Element("composer-dictate");
    dictate.parentElement = composer;
    const submit = new Element("composer-submit");
    submit.parentElement = composer;
    const status = new Element("dictation-status");
    status.parentElement = composer;
    nodes = { composer, prompt, dictate, submit, status };
  };
  let nodes;
  installComposer(current, options.draft ?? "");

  const dispatch = (type, event) => {
    for (const listener of documentListeners) {
      if (listener.type === type) listener.fn(event);
    }
  };
  const document = {
    readyState: "complete",
    activeElement: null,
    querySelector(selector) { return nodes[selector.slice(1)] ?? null; },
    addEventListener(type, fn) { documentListeners.push({ type, fn }); },
  };
  const sessionStorage = {
    getItem(key) { return storage.get(key) ?? null; },
    setItem(key, value) { storage.set(key, String(value)); },
    removeItem(key) { storage.delete(key); },
  };
  const window = {
    AudioContext: class {
      constructor() { this.sampleRate = 16_000; this.state = "running"; this.destination = {}; }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createScriptProcessor() {
        processor = { connect() {}, disconnect() {}, onaudioprocess: null };
        return processor;
      }
      createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
      async close() {}
    },
    addEventListener(type, fn) { windowListeners.push({ type, fn }); },
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() { return 1; },
    clearTimeout() {},
  };
  const navigator = {
    mediaDevices: {
      async getUserMedia() { return { getTracks: () => [{ stop() {} }] }; },
    },
  };
  const crypto = {
    randomUUID() {
      const id = leaseId(leaseSerial);
      leaseSerial += 1;
      return id;
    },
  };
  const fetch = async (path, init = {}) => {
    const call = { path: String(path), init };
    fetches.push(call);
    if (call.path.endsWith("/")) {
      return { ok: true, status: 200, json: async () => ({ state: "idle", capture: null }) };
    }
    if (call.path.endsWith("/start")) {
      const body = JSON.parse(init.body);
      call.jsonBody = body;
      return { ok: true, status: 200, json: async () => ({ state: "recording", capture: "local", boundSessionId: body.sessionId }) };
    }
    if (call.path.endsWith("/focus")) {
      return { ok: true, status: 200, json: async () => ({}) };
    }
    if (call.path.endsWith("/cancel")) {
      call.jsonBody = JSON.parse(init.body);
      return { ok: true, status: 200, json: async () => ({ state: "idle" }) };
    }
    if (call.path.endsWith("/end")) {
      if (endGate) await endGate.promise;
      return {
        ok: true,
        status: 200,
        json: async () => ({ sent: true, boundSessionId: A, text: "frozen draft speech" }),
      };
    }
    if (call.path.endsWith("/resume")) {
      return { ok: true, status: 200, json: async () => ({ state: "recording", capture: "local" }) };
    }
    throw new Error(`unexpected fetch ${path}`);
  };

  const sandbox = {
    ArrayBuffer,
    Blob,
    DataView,
    Float32Array,
    FormData,
    HTMLFormElement,
    HTMLTextAreaElement,
    Int16Array,
    Uint8Array,
    clearInterval: window.clearInterval,
    clearTimeout: window.clearTimeout,
    console,
    crypto,
    document,
    fetch,
    location: { pathname: `/qq/session/${current}` },
    navigator,
    sessionStorage,
    window,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "client.js" });

  return {
    fetches,
    storage,
    get nodes() { return nodes; },
    click(node) {
      dispatch("click", { target: node, preventDefault() {}, stopPropagation() {} });
    },
    switchTo(session, draft) { installComposer(session, draft); },
    releaseEnd() { endGate?.resolve(); },
    pagehide() {
      for (const listener of windowListeners) if (listener.type === "pagehide") listener.fn({ type: "pagehide" });
    },
  };
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function endCall(harness) {
  return harness.fetches.find((call) => call.path.endsWith("/end"));
}

test("client sends the frozen stored draft after switching before send and does not clear B", async () => {
  const keyA = `qq:composer:${A}`;
  const keyB = `qq:composer:${B}`;
  const harness = clientHarness({ storage: [[keyA, "A stored draft"], [keyB, "B stored draft"]] });
  await settle();
  harness.click(harness.nodes.dictate);
  await settle();
  harness.switchTo(B, "B visible draft");
  harness.click(harness.nodes.submit);
  await settle();

  const call = endCall(harness);
  assert.equal(call.init.headers["x-qq-dictation-protocol"], "server-delivery-v1");
  assert.equal(call.init.headers["content-type"], undefined, "the browser must supply the multipart boundary");
  assert.equal(await call.init.body.get("draft").text(), "A stored draft");
  assert.equal(call.init.body.get("audio").type, "audio/wav");
  assert.equal(harness.nodes.prompt.value, "B visible draft");
  assert.equal(harness.storage.get(keyB), "B stored draft");
  assert.equal(harness.storage.has(keyA), false);
});

test("same-session server delivery sends the visible draft and clears its composer and storage", async () => {
  const keyA = `qq:composer:${A}`;
  const harness = clientHarness({ draft: "same-session draft", storage: [[keyA, "saved copy"]] });
  await settle();
  harness.click(harness.nodes.dictate);
  await settle();
  harness.click(harness.nodes.submit);
  await settle();

  assert.equal(await endCall(harness).init.body.get("draft").text(), "same-session draft");
  assert.equal(harness.nodes.prompt.value, "");
  assert.equal(harness.storage.has(keyA), false);
});

test("pagehide still explicitly cancels an active recording", async () => {
  const harness = clientHarness();
  await settle();
  harness.click(harness.nodes.dictate);
  await settle();
  harness.pagehide();
  await settle();

  const cancels = harness.fetches.filter((entry) => entry.path.endsWith("/cancel"));
  assert.equal(cancels.length, 1);
  assert.equal(cancels[0].init.keepalive, true);
});

test("client freezes visible A draft at send, clears only A, and pagehide does not cancel transcription", async () => {
  const keyA = `qq:composer:${A}`;
  const keyB = `qq:composer:${B}`;
  const harness = clientHarness({ draft: "A visible draft", deferEnd: true, storage: [[keyA, "older A"], [keyB, "B stored"]] });
  await settle();
  harness.click(harness.nodes.dictate);
  await settle();
  harness.click(harness.nodes.submit);
  const call = endCall(harness);
  assert.ok(call, "the /end request must start in the send event turn");
  assert.equal(await call.init.body.get("draft").text(), "A visible draft");
  harness.switchTo(B, "B visible");
  harness.pagehide();
  assert.equal(harness.fetches.filter((entry) => entry.path.endsWith("/cancel")).length, 0);

  harness.releaseEnd();
  await settle();
  assert.equal(harness.nodes.prompt.value, "B visible");
  assert.equal(harness.storage.has(keyA), false);
  assert.equal(harness.storage.get(keyB), "B stored");
});
