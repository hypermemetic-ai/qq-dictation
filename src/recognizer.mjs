// Host-side Handy recognizer. The phone never runs a second engine; audio
// arrives here and this process asks the installed handy binary to transcribe.

import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { DictationError } from "./service.mjs";

export function defaultHandyBin(env = process.env) {
  const configured = String(env.QQ_DICTATION_HANDY ?? env.HANDY ?? "").trim();
  return configured || `${env.HOME ?? ""}/.local/bin/handy`;
}

function collect(child) {
  return new Promise((resolve, reject) => {
    const stdout = [];
    const stderr = [];
    child.stdout?.on("data", (chunk) => stdout.push(chunk));
    child.stderr?.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code, signal) => {
      resolve({
        code,
        signal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      });
    });
  });
}

function parseHandyText(stdout) {
  const raw = String(stdout ?? "").trim();
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.text === "string") return parsed.text;
  } catch {
    const match = raw.match(/^text:\s*(.*)$/m);
    if (match) return match[1];
  }
  return "";
}

const RIFF = Buffer.from("RIFF");
const WAVE = Buffer.from("WAVE");
const PCM_FORMAT = 1;
const DEFAULT_ENDPOINTING = Object.freeze({
  frameMs: 20,
  minSpeechMs: 120,
  paddingMs: 200,
  absoluteThresholdDb: -50,
  noiseMarginDb: 9,
  noisePercentile: 0.2,
  strongSpeechThresholdDb: -25,
  minActivityRangeDb: 6,
});

/** 16-bit PCM mono WAVE. handy --transcribe-file requires this container. */
export function encodePcm16MonoWav(pcm, sampleRate = 16_000) {
  const data = Buffer.isBuffer(pcm) ? pcm : Buffer.from(pcm ?? []);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(PCM_FORMAT, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

export function asWavBytes(audio, sampleRate = 16_000) {
  const bytes = Buffer.isBuffer(audio) ? audio : Buffer.from(audio ?? []);
  if (
    bytes.length >= 12
    && bytes.subarray(0, 4).equals(RIFF)
    && bytes.subarray(8, 12).equals(WAVE)
  ) {
    return bytes;
  }
  return encodePcm16MonoWav(bytes, sampleRate);
}

function pcm16MonoData(wav) {
  if (
    wav.length < 12
    || !wav.subarray(0, 4).equals(RIFF)
    || !wav.subarray(8, 12).equals(WAVE)
  ) return null;

  let format = null;
  let data = null;
  for (let offset = 12; offset + 8 <= wav.length;) {
    const id = wav.subarray(offset, offset + 4).toString("ascii");
    const size = wav.readUInt32LE(offset + 4);
    const start = offset + 8;
    const end = start + size;
    if (end > wav.length) return null;
    if (id === "fmt " && size >= 16 && !format) {
      format = {
        encoding: wav.readUInt16LE(start),
        channels: wav.readUInt16LE(start + 2),
        sampleRate: wav.readUInt32LE(start + 4),
        blockAlign: wav.readUInt16LE(start + 12),
        bitsPerSample: wav.readUInt16LE(start + 14),
      };
    } else if (id === "data" && !data) {
      data = wav.subarray(start, end);
    }
    offset = end + (size % 2);
  }

  if (
    !format
    || !data
    || format.encoding !== PCM_FORMAT
    || format.channels !== 1
    || format.bitsPerSample !== 16
    || format.blockAlign !== 2
    || !Number.isSafeInteger(format.sampleRate)
    || format.sampleRate < 1_000
    || data.length % 2 !== 0
  ) return null;
  return Object.freeze({ sampleRate: format.sampleRate, data });
}

function endpointOptions(options = {}) {
  const supplied = options && typeof options === "object" ? options : {};
  const finite = (name, minimum, maximum) => {
    const value = Number(supplied[name]);
    return Number.isFinite(value) && value >= minimum && value <= maximum
      ? value
      : DEFAULT_ENDPOINTING[name];
  };
  return Object.freeze({
    frameMs: finite("frameMs", 5, 100),
    minSpeechMs: finite("minSpeechMs", 20, 2_000),
    paddingMs: finite("paddingMs", 0, 2_000),
    absoluteThresholdDb: finite("absoluteThresholdDb", -90, -10),
    noiseMarginDb: finite("noiseMarginDb", 0, 30),
    noisePercentile: finite("noisePercentile", 0, 0.5),
    strongSpeechThresholdDb: finite("strongSpeechThresholdDb", -60, -10),
    minActivityRangeDb: finite("minActivityRangeDb", 0, 30),
  });
}

function frameLevels(data, frameSamples) {
  const sampleCount = data.length / 2;
  const levels = [];
  for (let start = 0; start < sampleCount; start += frameSamples) {
    const end = Math.min(sampleCount, start + frameSamples);
    let squareSum = 0;
    for (let sample = start; sample < end; sample += 1) {
      const value = data.readInt16LE(sample * 2);
      squareSum += value * value;
    }
    levels.push(Math.sqrt(squareSum / (end - start)));
  }
  return levels;
}

function percentile(values, proportion) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor((sorted.length - 1) * proportion)];
}

function supportedActivity(active, minimumFrames) {
  const supported = new Array(active.length).fill(false);
  // Speech may contain short unvoiced frames. Requiring 60% of a 120 ms
  // window retains ordinary syllables but excludes isolated handling clicks.
  const required = Math.max(1, Math.ceil(minimumFrames * 0.6));
  for (let start = 0; start + minimumFrames <= active.length; start += 1) {
    let count = 0;
    for (let index = start; index < start + minimumFrames; index += 1) {
      if (active[index]) count += 1;
    }
    if (count < required) continue;
    for (let index = start; index < start + minimumFrames; index += 1) {
      if (active[index]) supported[index] = true;
    }
  }
  return supported;
}

function rangeDb(lower, upper) {
  if (lower <= 0 || upper <= 0) return 0;
  return 20 * Math.log10(upper / lower);
}

function endpointFloor(levels, fromStart, probeFrames, absoluteThreshold, maximumRangeDb) {
  const count = Math.min(levels.length, probeFrames);
  const probe = fromStart ? levels.slice(0, count) : levels.slice(levels.length - count);
  const lower = percentile(probe, 0.2);
  const upper = percentile(probe, 0.8);

  // rangeDb(0, n) is intentionally undefined. Treat an all-zero/very-low probe
  // as stable, but do not mistake a mixture of silence and voiced frames for a
  // stationary endpoint merely because its lower percentile is zero.
  const stable = lower > 0
    ? rangeDb(lower, upper) <= maximumRangeDb
    : upper < absoluteThreshold;
  return Object.freeze({
    stable,
    floor: stable ? percentile(probe, 0.5) : null,
  });
}

function expandFromSeeds(levels, firstSeed, lastSeed, threshold, gapFrames) {
  let firstFrame = firstSeed;
  let gap = 0;
  for (let index = firstSeed - 1; index >= 0; index -= 1) {
    if (levels[index] >= threshold) {
      firstFrame = index;
      gap = 0;
    } else {
      gap += 1;
      if (gap > gapFrames) break;
    }
  }

  let lastFrame = lastSeed;
  gap = 0;
  for (let index = lastSeed + 1; index < levels.length; index += 1) {
    if (levels[index] >= threshold) {
      lastFrame = index;
      gap = 0;
    } else {
      gap += 1;
      if (gap > gapFrames) break;
    }
  }
  return Object.freeze({ firstFrame, lastFrame });
}

function detectSpeechBounds(levels, settings, minimumFrames) {
  const absoluteThreshold = 0x8000 * (10 ** (settings.absoluteThresholdDb / 20));
  const strongSpeechThreshold = 0x8000 * (10 ** (settings.strongSpeechThresholdDb / 20));
  const marginLinear = 10 ** (settings.noiseMarginDb / 20);

  // Estimate the two endpoint floors independently. Browser AGC commonly leaves
  // a quiet lead and a louder stationary tail, so the louder stable floor is the
  // conservative reference for speech seeds and boundaries.
  const probeFrames = Math.max(1, Math.ceil(300 / settings.frameMs));
  const stationaryRangeDb = Math.max(1, settings.minActivityRangeDb / 2);
  const endpoints = [
    endpointFloor(levels, true, probeFrames, absoluteThreshold, stationaryRangeDb),
    endpointFloor(levels, false, probeFrames, absoluteThreshold, stationaryRangeDb),
  ];
  const stableFloors = endpoints
    .filter((endpoint) => endpoint.stable)
    .map((endpoint) => endpoint.floor);
  // A strong constant voiced edge is stationary in RMS but is not a safe noise
  // estimate. Prefer the louder endpoint only among conservative noise-floor
  // candidates; this still handles RMS-40/RMS-400 AGC asymmetry without letting
  // strong speech at the opposite edge raise the seed threshold above itself.
  const stableNoiseFloors = stableFloors.filter((floor) => floor < strongSpeechThreshold);
  const endpointNoiseFloor = stableNoiseFloors.length ? Math.max(...stableNoiseFloors) : null;

  // Full-span speech makes any clip-wide low percentile part of the utterance.
  // Evaluate robust modulation recovery even if the ordinary gate finds central
  // peak islands; otherwise those islands become destructive trim boundaries.
  // A quiet stable endpoint disables recovery, preventing a real room-tone tail
  // from being relabeled as part of the utterance. Constant full-span voice is
  // preserved only at the deliberately conservative strong-energy threshold.
  const lowerLevel = percentile(levels, settings.noisePercentile);
  const upperLevel = percentile(levels, 1 - settings.noisePercentile);
  const hasQuietStableEndpoint = stableNoiseFloors.length > 0;
  const hasRobustModulation = lowerLevel >= absoluteThreshold
    && rangeDb(lowerLevel, upperLevel) >= settings.minActivityRangeDb;
  const hasRobustStrongEnergy = lowerLevel >= strongSpeechThreshold;
  const hasMinimumDuration = levels.length >= minimumFrames;
  if (hasMinimumDuration && !hasQuietStableEndpoint && (hasRobustModulation || hasRobustStrongEnergy)) {
    return Object.freeze({ firstFrame: 0, lastFrame: levels.length - 1 });
  }

  // Seed detection is intentionally stricter than boundary expansion. A seed
  // must be sustained and 9 dB over the louder endpoint floor (or a robust
  // clip-wide floor when neither endpoint is stationary). Thus a one-frame click
  // or 10 ms tap cannot promote the surrounding ambience to speech evidence.
  const seedNoiseFloor = endpointNoiseFloor ?? lowerLevel;
  const seedThreshold = Math.max(absoluteThreshold, seedNoiseFloor * marginLinear);
  const seeds = supportedActivity(
    levels.map((level) => level >= seedThreshold),
    minimumFrames,
  );
  const firstSeed = seeds.indexOf(true);
  const lastSeed = seeds.lastIndexOf(true);
  if (firstSeed < 0) return null;

  // Once a real seed exists, use a lower hysteresis threshold and tolerate short
  // unvoiced gaps. Sustained stationary endpoint runs remain below this threshold
  // and stop expansion; isolated terminal islands never became seeds above.
  const boundaryNoiseFloor = endpointNoiseFloor ?? seedNoiseFloor;
  const boundaryMargin = marginLinear ** (1 / 6); // 1.5 dB with the default 9 dB margin.
  const boundaryThreshold = Math.max(absoluteThreshold, boundaryNoiseFloor * boundaryMargin);
  return expandFromSeeds(levels, firstSeed, lastSeed, boundaryThreshold, minimumFrames);
}

/**
 * Reject no-speech and trim long non-speech endpoints before Handy's batch
 * decoder. Handy's file path has no microphone VAD, so forwarding room tone
 * lets the language model invent common or arbitrary continuations.
 *
 * Unknown/non-PCM WAVE formats are returned unchanged: a false negative only
 * loses mitigation, while guessing at their sample representation could lose
 * legitimate speech.
 */
export function prepareAudioForTranscription(audio, options = {}) {
  const wavBytes = asWavBytes(audio);
  const pcm = pcm16MonoData(wavBytes);
  if (!pcm) {
    return Object.freeze({ wavBytes, hasSpeech: null, trimmed: false });
  }
  if (pcm.data.length === 0) {
    return Object.freeze({ wavBytes, hasSpeech: false, trimmed: false });
  }

  const settings = endpointOptions(options);
  const frameSamples = Math.max(1, Math.round(pcm.sampleRate * settings.frameMs / 1_000));
  const minimumFrames = Math.max(1, Math.ceil(settings.minSpeechMs / settings.frameMs));
  const levels = frameLevels(pcm.data, frameSamples);
  const bounds = detectSpeechBounds(levels, settings, minimumFrames);
  if (!bounds) {
    return Object.freeze({ wavBytes, hasSpeech: false, trimmed: false });
  }

  const sampleCount = pcm.data.length / 2;
  const paddingSamples = Math.round(pcm.sampleRate * settings.paddingMs / 1_000);
  const firstSample = Math.max(0, (bounds.firstFrame * frameSamples) - paddingSamples);
  const lastSample = Math.min(sampleCount, ((bounds.lastFrame + 1) * frameSamples) + paddingSamples);
  if (firstSample === 0 && lastSample === sampleCount) {
    return Object.freeze({ wavBytes, hasSpeech: true, trimmed: false });
  }
  const trimmedData = pcm.data.subarray(firstSample * 2, lastSample * 2);
  return Object.freeze({
    wavBytes: encodePcm16MonoWav(trimmedData, pcm.sampleRate),
    hasSpeech: true,
    trimmed: true,
  });
}

export function createHandyRecognizer(config = {}) {
  const env = config.env ?? process.env;
  const handyBin = String(config.handyBin ?? defaultHandyBin(env));
  const spawnImpl = typeof config.spawn === "function" ? config.spawn : spawn;
  const timeoutMs = Number.isSafeInteger(config.timeoutMs) ? config.timeoutMs : 60_000;
  const endpointing = config.endpointing === false ? false : config.endpointing;

  return Object.freeze({
    handyBin,
    async recognize(audio) {
      const bytes = Buffer.isBuffer(audio) ? audio : Buffer.from(audio ?? []);
      if (bytes.length === 0) return "";
      const prepared = endpointing === false
        ? { wavBytes: asWavBytes(bytes), hasSpeech: null }
        : prepareAudioForTranscription(bytes, endpointing);
      if (prepared.wavBytes.length <= 44 || prepared.hasSpeech === false) return "";
      const dir = await mkdtemp(join(tmpdir(), "qq-dictate."));
      const wav = join(dir, "utterance.wav");
      try {
        await writeFile(wav, prepared.wavBytes);
        const child = spawnImpl(handyBin, ["--transcribe-file", wav, "--json"], {
          stdio: ["ignore", "pipe", "pipe"],
          env: {
            ...env,
            DISPLAY: String(env.DISPLAY ?? "").trim() || ":0",
            GDK_BACKEND: String(env.GDK_BACKEND ?? "").trim() || "x11",
            MESA_VK_DEVICE_SELECT: String(env.MESA_VK_DEVICE_SELECT ?? "").trim() || "1002:1900",
          },
        });
        const timer = setTimeout(() => {
          try { child.kill("SIGTERM"); } catch {}
        }, timeoutMs);
        timer.unref?.();
        let result;
        try {
          result = await collect(child);
        } finally {
          clearTimeout(timer);
        }
        if (result.code !== 0) {
          throw new DictationError(
            `qq-dictation: handy failed (${result.code ?? result.signal}): ${result.stderr.trim() || "no stderr"}`,
            503,
          );
        }
        return parseHandyText(result.stdout);
      } finally {
        await rm(dir, { recursive: true, force: true });
      }
    },
  });
}
