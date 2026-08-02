---
id: doc-7
title: 'TASK-10 research: CrisperWhisper 2.0 local dictation path'
type: other
created_date: '2026-08-02 16:03'
updated_date: '2026-08-02 18:15'
---
# TASK-10 research: CrisperWhisper 2.0 local dictation path

**Owning Task:** TASK-10
**Research/evaluation date:** 2026-08-02
**Overall confidence:** HIGH on package/API, measured CT2 CPU behavior, sidecar seams, completed rollback, physical PTT outcome, operator no-go disposition, and license text; MEDIUM on synthetic-speech quality; LOW on future dedicated-GPU behavior until that hardware exists.
**What this settles:** CrisperWhisper is not a Handy-selectable model or a ready Linux dictation application. A contained system-wide PTT sidecar is technically workable. On this machine, CT2 CPU/INT8 has a roughly six-to-eight-second post-release floor for short dictations; physical operator acceptance confirmed the path works and settled CPU adoption as a no-go because it is enormously slow. The 780M ROCm path proved operationally unsafe and was fully rolled back. The dedicated GPU is a later decision.

## Findings

### CrisperWhisper is a library, not a drop-in application

- **[HIGH — fact]** CrisperWhisper 2.0 exposes four standard models and explicit `verbatim`/`intended` modes through Python. Intended mode must be requested as `transcribe(..., mode="intended")`; verbatim is the default. [Official documentation](https://raw.githubusercontent.com/nyrahealth/CrisperWhisper/main/DOCS.md)
- **[HIGH — fact]** Turbo is 808,919,041 parameters and its current BF16 weights are 1,617,907,538 bytes. It is the vendor's fastest standard checkpoint. [Turbo metadata](https://huggingface.co/api/models/nyralabs/CrisperWhisper2.0_turbo?blobs=true)
- **[HIGH — fact]** The package ships no desktop UI, system-wide recorder, server entry point, or usable CLI. It supplies a Python library with selectable CT2 and Transformers backends. [Package metadata](https://raw.githubusercontent.com/nyrahealth/CrisperWhisper/main/pyproject.toml)
- **[MEDIUM — bounded search]** No searched candidate was simultaneously a local Linux dictation app, CrisperWhisper 2.0 intended-mode runtime, and Herdr delivery path. A Windows app uses a remote Nyra endpoint; Pi Voice STT still needs a local server and is not system-wide PTT; a specialized time-log app demonstrates only a local adapter. [Windows provider](https://raw.githubusercontent.com/3choff/dictate/main/src-tauri/src/providers/crisperwhisper.rs) [Pi Voice STT](https://raw.githubusercontent.com/cgarrot/pi-voice-stt/main/README.md) [Adapter example](https://raw.githubusercontent.com/economyofscale/voice-time-log-whisper/main/research-backend/main.py)

### Backend outcome

- **[HIGH — measured] CT2 CPU/INT8 works.** The custom fork advertises CPU and INT8 support; this host selected the AMD x86 path. The measured runtime was CrisperWhisper 2.0.1 with `ctranslate2-crisperwhisper` 4.7.1.post3 on Python 3.12.3. [CT2 hardware support](https://raw.githubusercontent.com/OpenNMT/CTranslate2/master/docs/hardware_support.md)
- **[HIGH — measured] ROCm was attempted rather than dismissed.** AMD's current matrix names the Ryzen 7 250/Radeon 780M (`gfx1103`). The official AMD PyTorch 2.12.0+ROCm 7.14 wheel saw the 780M and passed basic tensor work. CrisperWhisper required Transformers 4.57.3 rather than the then-current 5.x package to complete one inference. Repeated model inference then aborted in native HIP code with unspecified kernel-launch failures and destabilized the desktop. The operator stopped the GPU path.
- **[HIGH — rollback proof]** The GPU venv, GPU results/logs, crash dumps, and package cache were removed; no GPU process remained; the temporary `/dev/kfd` ACL was removed and the prior blocked access state re-verified. No system ROCm package was installed. Current-780M work is out of scope. [AMD compatibility matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html)

### Contained CPU setup and removal

The evaluation was contained beneath:

`~/.local/state/qq/task-10-crisperwhisper/runtime/`

Final footprint after conversion cleanup and before removal:

| Item | Size | Purpose |
| --- | ---: | --- |
| Lean runtime venv | 283 MB | CrisperWhisper 2.0.1, custom CT2 fork, audio libraries, python-xlib; no Torch or Transformers |
| Hugging Face turbo snapshot | 1.6 GB | Source model/config/tokenizer cache |
| Converted CT2 INT8 model | 783 MB | CPU runtime model |
| Synthetic audio/results | <5 MB | Evaluation evidence |
| **Total retained** | **2.6 GB** | Delete the one runtime root to remove the trial |

The one-time converter environment was removed after the INT8 cache and lean runtime were verified. The lean runtime reloaded the converted model and transcribed successfully without importable Torch or Transformers. After the operator settled CPU adoption as no-go and chose to preserve only the Repository prototype/report, the complete 2.6 GB runtime root was deleted. No sidecar process, model cache, venv, benchmark audio, result file, or temporary WAV remains.

### Measured latency on this Ryzen 7 250

Identical 16 kHz mono synthetic clips were transcribed with one warm turbo CT2 CPU/INT8 model in explicit intended mode. Run 2 is the repeated warm figure:

| Audio | Warm post-release wall time | Warm RTF | Observed behavior |
| ---: | ---: | ---: | --- |
| 5 s | 7.49 s | 1.50 | Slower than real time; short-clip floor dominates |
| 15 s | 8.43 s | 0.56 | Faster than real time, but eight-second wait after release |
| 30 s | 9.11 s | 0.30 | Usable batch speed; still a long interactive wait |
| 60 s | 22.40 s | 0.37 | Longform; first run was 28.07 s |

- **[HIGH — measured]** The benchmark process peaked at about 2.14 GB resident memory. The first conversion-plus-5-second command took 15.97 seconds end to end, peaked at 3.68 GB, and reported 6.36 seconds inside transcription.
- **[HIGH — conclusion]** For PTT use, audio duration is not the main issue below 30 seconds; release-to-text waits cluster around six to nine seconds. CPU is functional but not low-latency dictation.

### Intended-mode quality read on synthetic correction cases

This is a small safety read, not a speech benchmark. TTS pronunciation introduced recognizer errors, so results combine ASR and intended-mode behavior.

| Case | Intended output | Read |
| --- | --- | --- |
| Fillers | “I think we should send the report tomorrow morning.” | Good: removed “um/uh” without content loss |
| Clear correction | “Schedule the meeting for Tuesday, no wait, Wednesday at 9.30.” | Weak cleanup: correction marker and superseded Tuesday remained |
| False start | “I want to, can you please archive the old feature branch?” | Weak cleanup: abandoned fragment remained |
| Genuine contradiction | “Deploy to staging tonight. Do not deploy to staging tonight.” | Good safety: preserved both contradictory statements |
| Ambiguous correction | “You sport 8080? No, you sport 8081, or maybe keep 8080?” | Preserved ambiguity, but “use port” was misrecognized |
| Cross-sentence correction | “The release is on Thursday. That is wrong. The release is on Friday.” | Preserved the full correction sequence rather than reducing to Friday |
| Critical spans | “Open slash TMP slash report dot JSON, then run git checkout dash B feature slash audio.” | Preserved content but did not format the spoken path/flag |
| Already clean | “Please review the poll request before lunch.” | Harmful recognition error: “pull” became “poll” |

- **[MEDIUM — conclusion]** Intended mode clearly removed simple fillers and safely retained contradiction/ambiguity, but it did not clean the tested false start or corrections and introduced/retained recognition errors. These samples do not support calling the model a quality breakthrough for this workflow.

### Minimal system-wide PTT implementation

The Change adds one Python entry point and focused unit tests:

- `packaging/crisperwhisper-ptt.py`
- `tests/test_crisperwhisper_ptt.py`

The sidecar:

1. loads one explicit warm backend/model;
2. globally grabs a configurable X11 hold key (default `F9`, selected during hands-on acceptance and avoiding Handy's `Control_R`);
3. captures the exact focused Herdr pane at key-down;
4. records mono 16 kHz WAV through `ffmpeg` while held;
5. transcribes on release in intended mode;
6. delivers with bounded argv-only `herdr pane send-text`, optionally followed by Enter;
7. rejects missing targets, overlapping key state, unusable audio, empty output, inference errors, and delivery errors; and removes temporary WAVs.

**[HIGH — fresh Checks]** Twenty-five standard-library unit tests cover target parsing/capture, subprocess failures/timeouts, runtime-directory inspection failure, recorder startup-interrupt/validation/cleanup, ffmpeg's valid SIGINT exit status, intended file/benchmark modes, defensive numeric-result parsing, explicit model configuration, key/autorepeat state, bounded X11 waits, text argument safety, and submit ordering. The primary Python language server reported no diagnostics, and an active pi-lens scan reported no issues.

**[HIGH — runtime seams]** A real X11 grab loaded the warm CT2 model and exited cleanly on SIGTERM. A synthetic FIFO smoke exercised the production recorder, intended transcription, and real `herdr pane send-text`/Enter delivery into a disposable Herdr pane; the pane visibly received “This is a synthetic dictation benchmark for CRISPR whisper intended mode.”

**[HIGH — operator acceptance]** The operator then completed physical F9 hold-to-talk checks against the real microphone and Herdr target. A roughly two-second recording was delivered about six seconds after release; a roughly 33-second recording was delivered about 12 seconds after release; an accidental near-empty tap failed closed with “ffmpeg did not produce usable audio.” The operator's disposition was explicit: “Yeah, it worked. It was also enormously slow. If this is the performance we expect out of it, this is a no-go.” No transcript content was read or retained in the report, and temporary WAVs were removed.

### License boundary

- **[HIGH — fact]** Inference software is MIT, but model weights and generated outputs use Nyra's Non-Commercial Research License. It expressly includes evaluation and internal technical investigation while stating that Non-Commercial Use does not include “production or operational deployment.” [Turbo license](https://huggingface.co/nyralabs/CrisperWhisper2.0_turbo/raw/main/LICENSE.md)
- **[LOW — legal interpretation]** This trial is clearly evaluation. Ongoing everyday dictation may not be granted even if personal and non-commercial. Seek Nyra/legal clarification before auto-starting or treating the sidecar as an operational replacement.

## Recommendation

**No-go for CPU adoption.** Keep the sidecar only as a reviewed Repository prototype and reusable integration seam; do not auto-start it or replace Handy. The path works, but measured and hands-on release-to-text waits are too slow, and intended-mode cleanup was mixed. This is the operator's accepted disposition, not an inferred preference. The operator explicitly chose to land the inert prototype/report for the dedicated-GPU revisit while removing the entire local evaluation runtime.

Do not resume 780M ROCm work. When the dedicated GPU arrives, treat support and stability as a new measured decision; reuse the sidecar's explicit backend seam rather than adding GPU-specific code now. Do not add a desktop UI, service, generic server, or Handy changes unless later evidence justifies them.

## Sources

- [CrisperWhisper documentation](https://raw.githubusercontent.com/nyrahealth/CrisperWhisper/main/DOCS.md)
- [CrisperWhisper package metadata](https://raw.githubusercontent.com/nyrahealth/CrisperWhisper/main/pyproject.toml)
- [CrisperWhisper public model API](https://raw.githubusercontent.com/nyrahealth/CrisperWhisper/main/crisperwhisper/model.py)
- [CrisperWhisper converter](https://raw.githubusercontent.com/nyrahealth/CrisperWhisper/main/crisperwhisper/converter.py)
- [Turbo model metadata](https://huggingface.co/api/models/nyralabs/CrisperWhisper2.0_turbo?blobs=true)
- [Turbo model license](https://huggingface.co/nyralabs/CrisperWhisper2.0_turbo/raw/main/LICENSE.md)
- [CTranslate2 hardware support](https://raw.githubusercontent.com/OpenNMT/CTranslate2/master/docs/hardware_support.md)
- [AMD ROCm compatibility matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html)
- Local source: `packaging/handy-ptt-bridge.py`
- Local source: `src-tauri/src/target_binding.rs`
- Fresh local CPU/runtime/PTT Checks summarized in this report (temporary artifacts removed after operator-approved cleanup)

## Gaps

- **[HIGH] Dedicated GPU:** not present during this evaluation and explicitly deferred.
- **[MEDIUM] Synthetic quality:** eight TTS examples cannot represent the operator's speech, microphone, vocabulary, or all correction structures.
- **[HIGH] License:** ongoing operational personal dictation remains unclear.
- **[MEDIUM] Ready-made app search:** bounded public/official search can miss private, unindexed, or newly published applications.
