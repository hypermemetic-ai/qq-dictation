# @hypermemetic-ai/qq-dictation

Private ESM package providing an in-process Cordis voice-input plugin for the core DSH host.

## Commands

The package defines one lifecycle command:

```sh
npm test
```

It checks the syntax of [`src/plugin.mjs`](src/plugin.mjs) and [`src/client.js`](src/client.js), then runs Node's test runner. There is no package-defined start or development command.

## Repository map

[`package.json`](package.json) defines the public entry points and is the canonical package manifest:

| Package entry | Source |
| --- | --- |
| `@hypermemetic-ai/qq-dictation` | [`src/plugin.mjs`](src/plugin.mjs) |
| `@hypermemetic-ai/qq-dictation/service` | [`src/service.mjs`](src/service.mjs) |
| `@hypermemetic-ai/qq-dictation/http` | [`src/http.mjs`](src/http.mjs) |
| `@hypermemetic-ai/qq-dictation/recognizer` | [`src/recognizer.mjs`](src/recognizer.mjs) |

The DSH bundle metadata points to [`cordis.patch.yml`](cordis.patch.yml). The tracked test suite is [`test/dictation.test.mjs`](test/dictation.test.mjs). [`src/service.mjs`](src/service.mjs) has the highest relative-module fan-in, so changes there warrant particular attention to its importers and the test suite.

## Change routing

- Change a public package entry at the source mapped in the table, and keep [`package.json`](package.json) aligned if the export surface changes.
- Change DSH bundle patch configuration in [`cordis.patch.yml`](cordis.patch.yml).
- Add or update package-level coverage in [`test/dictation.test.mjs`](test/dictation.test.mjs), then run `npm test`.

Keep source as ESM: the manifest declares `"type": "module"`. Beyond the exported entry-point mapping above, consult the linked source before assuming component behavior; the repository metadata does not establish further runtime responsibilities.
