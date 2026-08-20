// qq-dictation: one repository, one plugin. Cordis entry point.
//
// Loading this plugin is how a DSH host gets voice input. Loading qq does
// not imply dictation. Bind is a DSH session id, not a pane id, and this
// package does not inherit a linked install root.

import { createDictateHandler } from "./http.mjs";
import { createHandyRecognizer } from "./recognizer.mjs";
import { createDictationService } from "./service.mjs";

export const name = "qq-dictation";
export const inject = ["qq", "webServer"];
export const provide = "qq-dictation";

export function apply(ctx, config = {}) {
  if (ctx.webServer.host !== "127.0.0.1") {
    throw new Error("qq-dictation: refusing a non-loopback web server");
  }
  const recognize = typeof config.recognize === "function"
    ? config.recognize
    : createHandyRecognizer(config).recognize;
  const service = createDictationService(ctx, { ...config, recognize });
  ctx.provide("qq-dictation", service);

  const basePath = String(config.basePath ?? "/qq/dictate");
  const handler = createDictateHandler(service, { basePath });
  ctx.effect(() => {
    const unregister = ctx.webServer.register({
      kind: "prefix",
      path: basePath,
      handler,
    });
    return async () => {
      unregister();
      await service.cancel();
    };
  }, "qq-dictation: HTTP routes and live bind");
}
