import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  createHandyRecognizer,
  encodePcm16MonoWav,
  prepareAudioForTranscription,
} from "../src/recognizer.mjs";

const RATE = 16_000;

function acousticFixture({
  leadingMs = 0,
  speechMs = 500,
  trailingMs = 0,
  noise = 0,
  leadingNoise = noise,
  speechNoise = noise,
  trailingNoise = noise,
  speechAmplitude = 7_500,
} = {}) {
  const leading = Math.round(RATE * leadingMs / 1_000);
  const speech = Math.round(RATE * speechMs / 1_000);
  const trailing = Math.round(RATE * trailingMs / 1_000);
  const pcm = Buffer.alloc((leading + speech + trailing) * 2);
  for (let index = 0; index < leading + speech + trailing; index += 1) {
    const ambient = index < leading
      ? leadingNoise
      : index < leading + speech
        ? speechNoise
        : trailingNoise;
    let sample = ambient ? (index % 2 === 0 ? ambient : -ambient) : 0;
    if (index >= leading && index < leading + speech) {
      const offset = index - leading;
      const envelope = 0.5 + (0.3 * Math.sin(2 * Math.PI * 4 * offset / RATE));
      sample += Math.round(speechAmplitude * envelope * Math.sin(2 * Math.PI * 190 * offset / RATE));
    }
    pcm.writeInt16LE(Math.max(-0x8000, Math.min(0x7fff, sample)), index * 2);
  }
  return { pcm, wav: encodePcm16MonoWav(pcm, RATE) };
}

function connectedSpeechFixture({ speechMs = 5_000, trailingMs = 0 } = {}) {
  const speech = Math.round(RATE * speechMs / 1_000);
  const trailing = Math.round(RATE * trailingMs / 1_000);
  const pcm = Buffer.alloc((speech + trailing) * 2);
  for (let index = 0; index < speech + trailing; index += 1) {
    // A continuously voiced carrier with speech-rate modulation and a nominal
    // peak of 5,000. The envelope never reaches zero.
    const envelope = (0.5 + (0.4 * Math.sin(2 * Math.PI * 8 * index / RATE))) / 0.9;
    const sample = index < speech
      ? Math.round(5_000 * envelope * Math.sin(2 * Math.PI * 190 * index / RATE))
      : index % 2 === 0 ? 400 : -400;
    pcm.writeInt16LE(sample, index * 2);
  }
  return encodePcm16MonoWav(pcm, RATE);
}

function wavDurationMs(wav) {
  assert.equal(wav.subarray(36, 40).toString("ascii"), "data");
  return wav.readUInt32LE(40) / 2 * 1_000 / wav.readUInt32LE(24);
}

function fakeSpawn(textForWav) {
  const calls = [];
  const spawn = (_bin, args) => {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => {};
    setImmediate(() => {
      try {
        const wav = readFileSync(args[1]);
        calls.push(wav);
        const text = typeof textForWav === "function" ? textForWav(wav) : textForWav;
        child.stdout.end(`${JSON.stringify({ text })}\n`);
        child.stderr.end();
        child.emit("close", 0, null);
      } catch (error) {
        child.emit("error", error);
      }
    });
    return child;
  };
  spawn.calls = calls;
  return spawn;
}

function lastSignalSample(wav, floor = 100) {
  const pcm = wav.subarray(44);
  for (let offset = pcm.length - 2; offset >= 0; offset -= 2) {
    if (Math.abs(pcm.readInt16LE(offset)) >= floor) return offset / 2;
  }
  return -1;
}

function withTerminalTransient(wav, { durationMs, amplitude }) {
  const changed = Buffer.from(wav);
  const samples = Math.max(1, Math.round(RATE * durationMs / 1_000));
  const dataStart = 44;
  const sampleCount = (changed.length - dataStart) / 2;
  for (let offset = 0; offset < samples; offset += 1) {
    const sample = sampleCount - samples + offset;
    changed.writeInt16LE(offset % 2 === 0 ? amplitude : -amplitude, dataStart + (sample * 2));
  }
  return changed;
}

test("no-speech audio never invokes the batch decoder", async () => {
  const spawn = fakeSpawn("Thank you.");
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });
  const silence = acousticFixture({ speechMs: 0, trailingMs: 2_000 }).wav;
  const lowRoomTone = acousticFixture({ speechMs: 0, trailingMs: 2_000, noise: 40 }).wav;
  // Reproduces QA's AGC-raised stationary ambience at RMS 400 (-38 dBFS).
  const raisedRoomTone = acousticFixture({ speechMs: 0, trailingMs: 30_000, noise: 400 }).wav;
  const raisedRoomToneWithClick = withTerminalTransient(raisedRoomTone, { durationMs: 0, amplitude: 20_000 });
  const raisedRoomToneWithTap = withTerminalTransient(raisedRoomTone, { durationMs: 10, amplitude: 2_000 });
  const raisedThenSilent = acousticFixture({
    leadingMs: 1_000,
    leadingNoise: 400,
    speechMs: 0,
    trailingMs: 1_000,
  }).wav;

  const clickPcm = Buffer.alloc(RATE * 2 * 2);
  clickPcm.writeInt16LE(20_000, clickPcm.length - 2);
  const isolatedClick = encodePcm16MonoWav(clickPcm, RATE);
  const standaloneTapPcm = Buffer.alloc(Math.round(RATE * 10 / 1_000) * 2);
  for (let offset = 0; offset < standaloneTapPcm.length; offset += 2) {
    standaloneTapPcm.writeInt16LE((offset / 2) % 2 === 0 ? 8_000 : -8_000, offset);
  }
  const standaloneTap = encodePcm16MonoWav(standaloneTapPcm, RATE);

  for (const [name, wav] of [
    ["silence", silence],
    ["low room tone", lowRoomTone],
    ["stationary RMS-400 room tone", raisedRoomTone],
    ["stationary RMS-400 room tone plus a terminal click", raisedRoomToneWithClick],
    ["stationary RMS-400 room tone plus a terminal 10 ms tap", raisedRoomToneWithTap],
    ["RMS-400 room tone followed by silence", raisedThenSilent],
    ["isolated click", isolatedClick],
    ["standalone 10 ms loud tap", standaloneTap],
  ]) {
    assert.equal(prepareAudioForTranscription(wav).hasSpeech, false, name);
    assert.equal(await recognizer.recognize(wav), "", name);
  }
  assert.equal(spawn.calls.length, 0, "no-speech must not reach Handy and generate its Thank you response");
});

test("endpointing trims silent and AGC-raised endpoints while retaining speech padding", async () => {
  const original = acousticFixture({ leadingMs: 600, speechMs: 600, trailingMs: 1_800 }).wav;
  const prepared = prepareAudioForTranscription(original);

  assert.equal(prepared.hasSpeech, true);
  assert.equal(prepared.trimmed, true);
  assert.ok(wavDurationMs(prepared.wavBytes) >= 950, "speech plus protective padding must remain");
  assert.ok(wavDurationMs(prepared.wavBytes) <= 1_100, "long non-speech endpoints must be removed");

  const raisedTail = acousticFixture({ speechMs: 5_000, trailingMs: 1_000, noise: 400 }).wav;
  const tailPrepared = prepareAudioForTranscription(raisedTail);
  assert.equal(tailPrepared.hasSpeech, true);
  assert.equal(tailPrepared.trimmed, true);
  assert.ok(wavDurationMs(tailPrepared.wavBytes) >= 5_000, "all voiced audio must remain");
  assert.ok(wavDurationMs(tailPrepared.wavBytes) <= 5_250, "only the protective pad may remain from the RMS-400 tail");

  const spawn = fakeSpawn("keep the spoken text");
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });
  assert.equal(await recognizer.recognize(raisedTail), "keep the spoken text");
  assert.equal(spawn.calls.length, 1);
  assert.ok(wavDurationMs(spawn.calls[0]) <= 5_250, "Handy must receive the trimmed endpoint");

  // The endpoint decision must not depend on a tail remaining a minority of
  // clip-wide percentiles. This is the field-risk shape from QA: once the
  // RMS-400 tail dominates, a global noise floor identifies it as activity.
  const dominantTail = acousticFixture({
    speechMs: 5_000,
    trailingMs: 30_000,
    noise: 400,
    speechAmplitude: 3_500,
  }).wav;
  const dominantPrepared = prepareAudioForTranscription(dominantTail);
  assert.equal(dominantPrepared.hasSpeech, true);
  assert.equal(dominantPrepared.trimmed, true);
  assert.ok(wavDurationMs(dominantPrepared.wavBytes) >= 4_900, "moderate conversational speech must survive");
  assert.ok(wavDurationMs(dominantPrepared.wavBytes) <= 5_250, "a dominant RMS-400 tail must be removed");

  // AGC can leave a quiet lead and then raise the post-speech room floor. A
  // quiet leading percentile must not collapse endpoint classification.
  const agcRamp = acousticFixture({
    leadingMs: 2_000,
    leadingNoise: 40,
    speechMs: 5_000,
    trailingMs: 2_000,
    trailingNoise: 400,
  }).wav;
  const rampPrepared = prepareAudioForTranscription(agcRamp);
  assert.equal(rampPrepared.hasSpeech, true);
  assert.equal(rampPrepared.trimmed, true);
  assert.ok(wavDurationMs(rampPrepared.wavBytes) >= 5_350);
  assert.ok(wavDurationMs(rampPrepared.wavBytes) <= 5_450, "only endpoint padding may remain around the AGC ramp");
});

test("batch decoder cannot generate a tail or no-speech phrase from discarded endpoint audio", async () => {
  const spawn = fakeSpawn((wav) => {
    const sampleCount = (wav.length - 44) / 2;
    const lastSignal = lastSignalSample(wav);
    if (lastSignal < 0) return "we chose the oxfords";
    const trailingMs = (sampleCount - lastSignal - 1) * 1_000 / RATE;
    return trailingMs > 800 ? "schedule the meeting thank you" : "schedule the meeting";
  });
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });

  // The field failures crossed Whisper's roughly 30-second decode window;
  // exercise that boundary without loading a model.
  const tailed = acousticFixture({ speechMs: 500, trailingMs: 32_000 }).wav;
  assert.ok(wavDurationMs(tailed) > 30_000);
  assert.equal(await recognizer.recognize(tailed), "schedule the meeting");
  assert.equal(spawn.calls.length, 1);
  assert.ok(wavDurationMs(spawn.calls[0]) < 1_000, "the batch decoder must not receive the 30s silent tail");

  // A dominant stationary tail must neither keep the full decoder window nor
  // make the utterance-wide stationarity check discard the real voiced span.
  for (const [speechMs, trailingMs] of [[500, 32_000], [700, 15_000]]) {
    const raisedTailed = acousticFixture({ speechMs, trailingMs, noise: 400 }).wav;
    assert.equal(await recognizer.recognize(raisedTailed), "schedule the meeting");
    const decoded = spawn.calls.at(-1);
    assert.ok(wavDurationMs(decoded) < 1_200, `${speechMs} ms speech must survive while its RMS-400 tail is trimmed`);
  }
  assert.equal(spawn.calls.length, 3, "each real utterance must invoke the decoder once");

  // A long tail owns clip-wide p20. Moderate speech must still be recognized:
  // measuring modulation only above the final adaptive cut truncates its lower
  // envelope and incorrectly makes the remaining peaks look stationary.
  for (const trailingMs of [15_000, 30_000]) {
    const moderateTailed = acousticFixture({
      speechMs: 5_000,
      trailingMs,
      noise: 400,
      speechAmplitude: 3_500,
    }).wav;
    const prepared = prepareAudioForTranscription(moderateTailed);
    assert.equal(
      prepared.hasSpeech,
      true,
      `moderate speech plus ${trailingMs} ms raised tail`,
    );
    assert.equal(prepared.trimmed, true);
    assert.ok(wavDurationMs(prepared.wavBytes) >= 4_900, "moderate voiced span must survive");
    assert.ok(wavDurationMs(prepared.wavBytes) <= 5_250, "dominant raised tail must be trimmed");
    assert.equal(await recognizer.recognize(moderateTailed), "schedule the meeting");
    assert.ok(wavDurationMs(spawn.calls.at(-1)) <= 5_250, "Handy must receive only the voiced span");
  }
  assert.equal(spawn.calls.length, 5, "moderate tailed utterances must each invoke the decoder once");

  // Regress the same duration-dependent failure with a second connected-AM
  // shape whose nominal peak is 5,000: the old gate kept identical speech full
  // span and with a short tail, but discarded it once RMS-400 owned p20.
  for (const trailingMs of [0, 1_000, 15_000, 30_000]) {
    const connected = connectedSpeechFixture({ trailingMs });
    const prepared = prepareAudioForTranscription(connected);
    assert.equal(
      prepared.hasSpeech,
      true,
      `connected speech plus ${trailingMs} ms raised tail`,
    );
    assert.ok(wavDurationMs(prepared.wavBytes) >= 4_900, "connected voiced span must survive");
    assert.ok(wavDurationMs(prepared.wavBytes) <= 5_250, "connected-speech tail must be trimmed");
    assert.equal(await recognizer.recognize(connected), "schedule the meeting");
  }
  assert.equal(spawn.calls.length, 9, "every connected-speech fixture must invoke the decoder once");

  const longRaisedTail = acousticFixture({
    speechMs: 5_000,
    trailingMs: 30_000,
    noise: 400,
    speechAmplitude: 3_500,
  }).wav;
  for (const [name, tailedWithTransient] of [
    ["terminal click", withTerminalTransient(longRaisedTail, { durationMs: 0, amplitude: 20_000 })],
    ["terminal 10 ms tap", withTerminalTransient(longRaisedTail, { durationMs: 10, amplitude: 2_000 })],
  ]) {
    const beforeCalls = spawn.calls.length;
    assert.equal(await recognizer.recognize(tailedWithTransient), "schedule the meeting", name);
    assert.equal(spawn.calls.length, beforeCalls + 1, `${name} utterance must invoke Handy once`);
    assert.ok(
      wavDurationMs(spawn.calls.at(-1)) < 5_500,
      `${name} must not turn the 30-second noisy tail into a final speech island`,
    );
  }

  const silent = acousticFixture({ speechMs: 0, trailingMs: 2_000 }).wav;
  assert.equal(await recognizer.recognize(silent), "");
  assert.equal(spawn.calls.length, 11, "no-speech must not invoke the generative decoder");
});

test("voiced provider text is not phrase-filtered, including a legitimate thank you", async () => {
  const spoken = acousticFixture({ leadingMs: 100, speechMs: 700, trailingMs: 900 }).wav;
  const thanksSpawn = fakeSpawn("Please save this, thank you.");
  const thanks = createHandyRecognizer({ handyBin: "fake-handy", spawn: thanksSpawn });
  assert.equal(await thanks.recognize(spoken), "Please save this, thank you.");

  const unusualSpawn = fakeSpawn("We chose the Oxfords.");
  const unusual = createHandyRecognizer({ handyBin: "fake-handy", spawn: unusualSpawn });
  assert.equal(await unusual.recognize(spoken), "We chose the Oxfords.");
});

test("raw PCM is gated after wrapping and unsupported WAVE formats pass through safely", async () => {
  const { pcm } = acousticFixture({ speechMs: 500, trailingMs: 1_000 });
  const preparedRaw = prepareAudioForTranscription(pcm);
  assert.equal(preparedRaw.hasSpeech, true);
  assert.equal(preparedRaw.wavBytes.subarray(0, 4).toString("ascii"), "RIFF");
  assert.ok(wavDurationMs(preparedRaw.wavBytes) < 800);

  const stereo = encodePcm16MonoWav(Buffer.alloc(320), RATE);
  stereo.writeUInt16LE(2, 22);
  stereo.writeUInt32LE(RATE * 4, 28);
  stereo.writeUInt16LE(4, 32);
  const unsupported = prepareAudioForTranscription(stereo);
  assert.equal(unsupported.hasSpeech, null);
  assert.equal(unsupported.trimmed, false);
  assert.deepEqual(unsupported.wavBytes, stereo);

  const spawn = fakeSpawn("stereo speech");
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });
  assert.equal(await recognizer.recognize(stereo), "stereo speech");
  assert.deepEqual(spawn.calls[0], stereo);
});

function constantVoicedWav({
  leadingMs = 0,
  speechMs = 500,
  trailingMs = 0,
  amplitude = 8_000,
  trailingNoise = 0,
} = {}) {
  const leading = Math.round(RATE * leadingMs / 1_000);
  const speech = Math.round(RATE * speechMs / 1_000);
  const trailing = Math.round(RATE * trailingMs / 1_000);
  const pcm = Buffer.alloc((leading + speech + trailing) * 2);
  for (let index = 0; index < leading + speech + trailing; index += 1) {
    let sample = 0;
    if (index >= leading && index < leading + speech) {
      sample = Math.round(amplitude * Math.sin(2 * Math.PI * 190 * (index - leading) / RATE));
    } else if (index >= leading + speech && trailingNoise) {
      sample = index % 2 === 0 ? trailingNoise : -trailingNoise;
    }
    pcm.writeInt16LE(sample, index * 2);
  }
  return encodePcm16MonoWav(pcm, RATE);
}

test("short connected full-span speech is retained whole even when the primary gate finds only peaks", async () => {
  const spawn = fakeSpawn("connected speech");
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });
  for (const speechMs of [600, 800]) {
    const original = connectedSpeechFixture({ speechMs });
    const prepared = prepareAudioForTranscription(original);
    assert.equal(prepared.hasSpeech, true, `${speechMs} ms connected speech`);
    assert.equal(prepared.trimmed, false, `${speechMs} ms connected speech must not be peak-chopped`);
    assert.deepEqual(prepared.wavBytes, original, `${speechMs} ms full-span speech must pass whole`);
    assert.equal(await recognizer.recognize(original), "connected speech");
    assert.deepEqual(spawn.calls.at(-1), original, "Handy must receive the complete connected utterance");
  }
  assert.equal(spawn.calls.length, 2);
});

test("asymmetric AGC endpoint floors do not hide a short moderate utterance", async () => {
  const spawn = fakeSpawn("short moderate utterance");
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });

  for (const speechMs of [400, 500, 600, 700]) {
    const original = acousticFixture({
      leadingMs: 2_000,
      leadingNoise: 40,
      speechMs,
      speechAmplitude: 3_500,
      trailingMs: 2_000,
      trailingNoise: 400,
    }).wav;
    const prepared = prepareAudioForTranscription(original);
    assert.equal(prepared.hasSpeech, true, `${speechMs} ms moderate speech between asymmetric floors`);
    assert.equal(prepared.trimmed, true);
    assert.ok(
      wavDurationMs(prepared.wavBytes) >= speechMs + 350,
      `${speechMs} ms utterance and protective endpoint padding must remain`,
    );
    assert.ok(
      wavDurationMs(prepared.wavBytes) <= speechMs + 450,
      `${speechMs} ms utterance must be separated from both stationary endpoints`,
    );
    assert.equal(await recognizer.recognize(original), "short moderate utterance");
  }
  assert.equal(spawn.calls.length, 4, "each moderate utterance must invoke Handy exactly once");
});

test("endpointing keeps short and full-span voiced audio", async () => {
  const spawn = fakeSpawn("schedule the meeting thank you");
  const recognizer = createHandyRecognizer({ handyBin: "fake-handy", spawn });

  // These durations directly regress the adaptive-percentile false negative:
  // with no endpoint silence, the low percentile belongs to the voiced signal.
  for (const speechMs of [120, 500, 1_000]) {
    const unpadded = constantVoicedWav({ speechMs });
    const prepared = prepareAudioForTranscription(unpadded);
    assert.equal(prepared.hasSpeech, true, `${speechMs} ms uninterrupted voice`);
    assert.equal(prepared.trimmed, false);
    assert.equal(await recognizer.recognize(unpadded), "schedule the meeting thank you");
  }

  // A stationary-looking strong voiced edge must not become the noise estimate:
  // the quieter stable tail remains authoritative and must still be trimmed.
  const constantRaisedTail = constantVoicedWav({
    speechMs: 5_000,
    trailingMs: 1_000,
    trailingNoise: 400,
  });
  const constantTailPrepared = prepareAudioForTranscription(constantRaisedTail);
  assert.equal(constantTailPrepared.hasSpeech, true, "constant full-span voice must survive");
  assert.equal(constantTailPrepared.trimmed, true);
  assert.ok(wavDurationMs(constantTailPrepared.wavBytes) >= 5_000);
  assert.ok(wavDurationMs(constantTailPrepared.wavBytes) <= 5_250, "stable endpoint gate must trim RMS-400");
  assert.equal(await recognizer.recognize(constantRaisedTail), "schedule the meeting thank you");

  const sendGesture = constantVoicedWav({ leadingMs: 100, speechMs: 5_000, trailingMs: 200 });
  const sendPrepared = prepareAudioForTranscription(sendGesture);
  assert.equal(sendPrepared.hasSpeech, true, "record-until-send dictation must remain transcribable");
  assert.ok(wavDurationMs(sendPrepared.wavBytes) >= 5_000, "spoken span must be preserved");
  assert.equal(await recognizer.recognize(sendGesture), "schedule the meeting thank you");
  assert.equal(spawn.calls.length, 5, "every voiced fixture must reach Handy exactly once");

  // Below the strong-signal threshold, speech-like RMS modulation is sufficient
  // while the stationary RMS-400 regression above remains no-speech.
  const quietSpeech = acousticFixture({ speechMs: 500, speechAmplitude: 900 }).wav;
  assert.equal(prepareAudioForTranscription(quietSpeech).hasSpeech, true, "quiet modulated speech");
  assert.equal(await recognizer.recognize(quietSpeech), "schedule the meeting thank you");
  assert.equal(spawn.calls.length, 6);
});
