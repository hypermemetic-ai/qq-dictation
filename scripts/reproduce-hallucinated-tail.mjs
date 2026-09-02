// Deterministic reproduction for batch decoders that generate text from long
// room-tone endpoints. The fake provider examines the actual WAVE file handed
// to Handy.
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { PassThrough } from "node:stream";

import { createHandyRecognizer, encodePcm16MonoWav } from "../src/recognizer.mjs";

const RATE = 16_000;

function pcmWithTail({
  speechMs = 450,
  tailMs = 52_800,
  tailAmplitude = 0,
  speechAmplitude = 8_000,
} = {}) {
  const speechSamples = Math.round(RATE * speechMs / 1_000);
  const tailSamples = Math.round(RATE * tailMs / 1_000);
  const pcm = Buffer.alloc((speechSamples + tailSamples) * 2);
  for (let i = 0; i < speechSamples; i += 1) {
    // Modulated voiced energy; this is an acoustic fixture, not real speech.
    const envelope = 0.45 + (0.35 * Math.sin(2 * Math.PI * 3 * i / RATE));
    pcm.writeInt16LE(Math.round(speechAmplitude * envelope * Math.sin(2 * Math.PI * 180 * i / RATE)), i * 2);
  }
  for (let i = speechSamples; i < speechSamples + tailSamples; i += 1) {
    pcm.writeInt16LE(i % 2 === 0 ? tailAmplitude : -tailAmplitude, i * 2);
  }
  return encodePcm16MonoWav(pcm, RATE);
}

function withTerminalTransient(wav, { durationMs, amplitude }) {
  const changed = Buffer.from(wav);
  const samples = Math.max(1, Math.round(RATE * durationMs / 1_000));
  const sampleCount = (changed.length - 44) / 2;
  for (let offset = 0; offset < samples; offset += 1) {
    const sample = sampleCount - samples + offset;
    changed.writeInt16LE(offset % 2 === 0 ? amplitude : -amplitude, 44 + (sample * 2));
  }
  return changed;
}

function fakeBatchDecoder() {
  return (_bin, args) => {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => {};
    setImmediate(() => {
      const wav = readFileSync(args[1]);
      const pcm = wav.subarray(44);
      let lastSignal = -1;
      for (let i = 0; i + 1 < pcm.length; i += 2) {
        if (Math.abs(pcm.readInt16LE(i)) >= 100) lastSignal = i / 2;
      }
      const samples = pcm.length / 2;
      const durationMs = samples * 1_000 / RATE;
      const trailingSilenceMs = lastSignal < 0 ? Infinity : (samples - lastSignal - 1) * 1_000 / RATE;
      const text = lastSignal < 0
        ? "we chose the oxfords"
        : durationMs > 30_000 || trailingSilenceMs > 800
          ? "set a timer thank you"
          : "set a timer";
      child.stdout.end(`${JSON.stringify({ text })}\n`);
      child.stderr.end();
      child.emit("close", 0, null);
    });
    return child;
  };
}

const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn: fakeBatchDecoder() });
const tailed = await recognizer.recognize(pcmWithTail());
const raisedTailed = await recognizer.recognize(pcmWithTail({
  speechMs: 500,
  tailMs: 32_000,
  tailAmplitude: 400,
}));
const moderateRaisedTailed = await recognizer.recognize(pcmWithTail({
  speechMs: 5_000,
  tailMs: 30_000,
  tailAmplitude: 400,
  speechAmplitude: 3_500,
}));
const silent = await recognizer.recognize(pcmWithTail({ speechMs: 0, tailMs: 2_000 }));
const raisedRoomTone = await recognizer.recognize(pcmWithTail({
  speechMs: 0,
  tailMs: 30_000,
  tailAmplitude: 400,
}));
const raisedRoomToneWithClick = await recognizer.recognize(withTerminalTransient(
  pcmWithTail({ speechMs: 0, tailMs: 30_000, tailAmplitude: 400 }),
  { durationMs: 0, amplitude: 20_000 },
));
const raisedRoomToneWithTap = await recognizer.recognize(withTerminalTransient(
  pcmWithTail({ speechMs: 0, tailMs: 30_000, tailAmplitude: 400 }),
  { durationMs: 10, amplitude: 2_000 },
));
const longRaisedTail = pcmWithTail({ speechMs: 5_000, tailMs: 30_000, tailAmplitude: 400 });
const raisedTailedWithClick = await recognizer.recognize(withTerminalTransient(
  longRaisedTail,
  { durationMs: 0, amplitude: 20_000 },
));
const raisedTailedWithTap = await recognizer.recognize(withTerminalTransient(
  longRaisedTail,
  { durationMs: 10, amplitude: 2_000 },
));
const spoken = await recognizer.recognize(pcmWithTail({ speechMs: 5_000, tailMs: 0 }));

const results = {
  tailed,
  raisedTailed,
  moderateRaisedTailed,
  silent,
  raisedRoomTone,
  raisedRoomToneWithClick,
  raisedRoomToneWithTap,
  raisedTailedWithClick,
  raisedTailedWithTap,
  spoken,
};
console.log(JSON.stringify(results, null, 2));
const noSpeechLeaked = [silent, raisedRoomTone, raisedRoomToneWithClick, raisedRoomToneWithTap]
  .some((text) => text !== "");
const tailLeaked = [tailed, raisedTailed, moderateRaisedTailed, raisedTailedWithClick, raisedTailedWithTap]
  .some((text) => text !== "set a timer");
if (noSpeechLeaked || tailLeaked) {
  console.error("REPRODUCED: batch endpoint audio reached the decoder and generated text.");
  process.exitCode = 1;
} else if (spoken !== "set a timer") {
  console.error("REGRESSED: full-span speech was discarded before the decoder.");
  process.exitCode = 1;
} else {
  console.log("MITIGATED: silent/noisy endpoints and terminal transients were discarded, no-speech skipped the decoder, and full-span speech was kept.");
}
