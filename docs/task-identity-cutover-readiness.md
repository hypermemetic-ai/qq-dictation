# Task identity cutover readiness

## Scope and result

This receipt prepares qq-dictation for one configured Task prefix at a time. It
does not activate the coordinated cutover. The checker reads the configured
prefix, validates one ASCII-letters-only scalar, and derives the display form by
uppercasing that scalar. It has no prefix allow-list, alias, compatibility map,
or configuration writer.

The exact preparation base is
`0b7fd8e6b6859c1ed92ae6e8950c5d5349d811d5`. The readiness result is the one
commit with subject `chore: prepare adaptive task identity cutover` that carries
this receipt. Its immutable hash is recorded in the completion envelope after
the commit exists; a commit cannot include its own hash.

The inventory unit is one source line containing one or more of either exact
legacy scanner word: the all-capital legacy Task-identity word or the capital-C
legacy lifecycle word. Multiple matching words on one line share one
classification. The base had exactly 18 such lines in eight files.

## Base occurrence classification and disposition

### Migrated identity and authority lines

These seven base lines required migration. They no longer match the legacy-word
scan at the prepared source head.

| Base path and line                        | Category          | Prepared wording or reference                                                |
| ----------------------------------------- | ----------------- | ---------------------------------------------------------------------------- |
| `.pi/prompts/dictation-review.md:129`     | current authority | A repository Task must land through GitHub Flow.                             |
| `.pi/prompts/dictation-review.md:131`     | current authority | A runtime-only dictionary update needs no repository Task.                   |
| `BUILD-LESSONS.md:13`                     | current authority | The accountable role is Adaptive Task Owner.                                 |
| `BUILD-LESSONS.md:103`                    | current reference | Green evidence comes from the mounted Task worktree.                         |
| `packaging/REMOTE-LAPTOP-DICTATION.md:25` | current authority | The reviewed Task controls the live-install gate.                            |
| `scripts/build-local.sh:18`               | current reference | The build comment refers to Task worktrees.                                  |
| `scripts/build-local.sh:31`               | current reference | The safeguard refers to the configuration-neutral July containment evidence. |

### Preserved lines

At the prepared source head, the remaining 11 base lines were intentionally
byte-preserved.

| Base and prepared path and line            | Category                     | Reason                                                              |
| ------------------------------------------ | ---------------------------- | ------------------------------------------------------------------- |
| `BUILD-LESSONS.md:27`                      | explicit historical evidence | Completed record 3 and its dated build evidence.                    |
| `BUILD-LESSONS.md:45`                      | explicit historical evidence | The first record 6 isolated-worktree event and record 14 follow-up. |
| `BUILD-LESSONS.md:53`                      | explicit historical evidence | Completed record 6 and record 14 source evidence.                   |
| `BUILD-LESSONS.md:95`                      | explicit historical evidence | Record 14 toolchain-discovery stage.                                |
| `BUILD-LESSONS.md:101`                     | explicit historical evidence | Record 3 resource-limit provenance.                                 |
| `BUILD-LESSONS.md:105`                     | explicit historical evidence | Dated record 14 failure heading.                                    |
| `BUILD-LESSONS.md:123`                     | explicit historical evidence | Record 14 constructor-failure cause.                                |
| `CONTRIBUTING_TRANSLATIONS.md:124`         | ordinary product prose       | Translation-edit instruction.                                       |
| `src-tauri/src/shortcut/mod.rs:250`        | ordinary product prose       | Keyboard API documentation.                                         |
| `src/bindings.ts:395`                      | ordinary product prose       | Generated keyboard API documentation.                               |
| `src/i18n/locales/en/translation.json:626` | ordinary product prose       | User-interface language description.                                |

Present state: the public translation guide and its ordinary-prose occurrence
have since been retired. The translated user-interface occurrence has also been
retired. The two keyboard API documentation lines remain byte-identical at
their current line numbers, and three product documents now carry current
references to the workstation/laptop boundary.

For the prepared result, the current authority and current reference categories
above record every migrated line; the ordinary product prose and explicit
historical evidence categories below record every preserved line. No application
source under `src/**` or `src-tauri/**` was edited by that preparation.

## Machine-checked prepared-source inventory

Each marker classifies one matching prepared-source line by path, line number,
and SHA-256 of the line without its newline. The checker compares this exact set
with committed, staged, and untracked candidate files, skips symlinks and binary
files, and excludes the mounted `backlog` store. A new, altered, missing, or
stale occurrence fails the check. The current tree has exactly 15 matching lines
in six files: ten explicit historical-evidence lines, three current-reference
lines, and two ordinary-product-prose lines.

<!-- identity-readiness: {"category":"explicit historical evidence","digest":"afb8fcde6474ae343c6528b248f23feabb5e2264e296e357b3575b290f65420e","line":27,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"3110d12beafb3bd1475cb367a13679d5cc6b081b890bddf053caa516741bfafe","line":41,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"67656f1d27a55aaff92c838ffcd35c435cf6025819c47eef6dbaa08aa7883b46","line":57,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"705b433befb328facb6c406803f69056349fc6d18bb363f9c38c0e6b30dfd3be","line":63,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"6d27374f28395c0b33ba5c7142d6057662f109b961b51befc72065299434e321","line":67,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"0f72729eb72737819c95b8b5e264d851f2ae241878707e08374a95cd2ab9c722","line":77,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"20f509b3659865ecb0cad025b138176295b151dc8127a901fce091a80b40620f","line":119,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"2a0b97538674e5b88de39390dfa5327f6cf59f2598ae9892233d5a293340bf61","line":125,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"e426bf4a2c7bff5524521ca29d20203fadfda7d72d6015afbfbff044c2fba7c0","line":129,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"explicit historical evidence","digest":"8f0f10501887e82599d678db9989ef79651a2bedac827249551e31d6a395cb28","line":147,"path":"BUILD-LESSONS.md"} -->
<!-- identity-readiness: {"category":"current reference","digest":"19d1a5b1a716a966efbb756189c1fe9c9af5100f3266d004232f136f18f28be9","line":72,"path":"README.md"} -->
<!-- identity-readiness: {"category":"current reference","digest":"817ff41ba9b7d90333ef63fe4e7f37493e92aa9d799835a29a39f238feac79f9","line":100,"path":"docs/local-distribution.md"} -->
<!-- identity-readiness: {"category":"current reference","digest":"0026f042f4e12f2e54e1b770b0a76e4cd3ce018f37a443a83c1a51ff6f8dec23","line":15,"path":"docs/project-concepts.md"} -->
<!-- identity-readiness: {"category":"ordinary product prose","digest":"41fa587afb4ab771a04333741a61783feb75a72dd1abb9482db1a23c11bf650f","line":247,"path":"src-tauri/src/shortcut/mod.rs"} -->
<!-- identity-readiness: {"category":"ordinary product prose","digest":"8ee51b4a1c8c4297abade8189db8b548233d13afc70e1a3c98a87878b200804a","line":363,"path":"src/bindings.ts"} -->

## Preparation versus activation

This result changes repository wording, adds a read-only checker, deterministic
tests, and this receipt. It does not edit `backlog/config.yml`, managed Backlog
Markdown, application code, settings, history, audio, providers, installed
files, services, processes, or runtime state. The current configured prefix
therefore remains unchanged. The central cutover alone changes the mounted
collection configuration and records.

The same source is ready when a fixture config supplies `task` and when another
fixture supplies `a`; exactly one is accepted in either run. A malformed,
duplicated, or multi-value scalar is refused before scanning source.

## Exact changed paths

- `.pi/prompts/dictation-review.md`
- `BUILD-LESSONS.md`
- `docs/task-identity-cutover-readiness.md`
- `packaging/REMOTE-LAPTOP-DICTATION.md`
- `scripts/build-local.sh`
- `scripts/check-task-identity-readiness.py`
- `tests/test_task_identity_readiness.py`

No generated, cache, application, lockfile, live-state, or mounted-store path is
part of the result.

## Check facts

The assigned checks passed on the final committed source:

- The configured-current owner fixture passed and derived the uppercase display
  form from `task`.
- The configured-`a` owner fixture passed and derived display prefix `A` from
  the same source.
- The ambiguous/multi-value owner fixture was refused, so its invalid-mode
  acceptance check passed.
- Direct focused readiness execution ran 6 tests; all passed.
- The dependency-free behavior subset ran 25, 3, 5, and 11 tests; all 44
  passed.
- `bash -n scripts/build-local.sh` exited zero without output.
- The exact-base `git diff --check` exited zero without output.
- Final `git status --porcelain=v2 --untracked-files=all` exited zero without
  output.

## Rollback

Rollback is one Git revert of the single readiness commit named above. That
revert removes the checker, tests, receipt, and seven current-wording edits. No
configuration, store, installer, application-state, or process rollback is
needed because preparation performs no activation or live mutation. If the
central cutover has later activated a new prefix, its separate store/config
rollback must occur under the central cutover plan before reverting this
readiness source.
