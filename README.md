# @hypermemetic-ai/qq-dictation

Private, in-process Cordis voice-input plugin for the core DSH host.

The package is ESM. Its default entry point is [`src/plugin.mjs`](src/plugin.mjs), and its DSH bundle metadata points to [`cordis.patch.yml`](cordis.patch.yml).

## Commands

```sh
npm test
npm run reproduce:tail
```

`npm test` syntax-checks every source entry point and then runs the Node test suite. `reproduce:tail` runs [`scripts/reproduce-hallucinated-tail.mjs`](scripts/reproduce-hallucinated-tail.mjs). No start script is defined.

## Repository map

The package export map identifies the main source boundaries:

- Package root: [`src/plugin.mjs`](src/plugin.mjs)
- `./service`: [`src/service.mjs`](src/service.mjs)
- `./http`: [`src/http.mjs`](src/http.mjs)
- `./recognizer`: [`src/recognizer.mjs`](src/recognizer.mjs)

[`src/service.mjs`](src/service.mjs) has the highest relative-import fan-in, followed by the HTTP and recognizer modules, so changes there may affect multiple importers. [`src/client.js`](src/client.js) is another source file, but the package metadata does not expose it as a package subpath.

## Change routing

- For package entry points, scripts, or published-file selection, start with [`package.json`](package.json).
- For DSH bundle wiring, start with [`cordis.patch.yml`](cordis.patch.yml).
- For recognizer work, start with [`src/recognizer.mjs`](src/recognizer.mjs) and [`test/recognizer.test.mjs`](test/recognizer.test.mjs).
- For broader dictation changes, use [`test/dictation.test.mjs`](test/dictation.test.mjs) and the exported source boundary involved.
- For the hallucinated-tail reproduction, use [`scripts/reproduce-hallucinated-tail.mjs`](scripts/reproduce-hallucinated-tail.mjs).
