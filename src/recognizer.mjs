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

/** 16-bit PCM mono WAVE. handy --transcribe-file requires this container. */
export function encodePcm16MonoWav(pcm, sampleRate = 16_000) {
  const data = Buffer.isBuffer(pcm) ? pcm : Buffer.from(pcm ?? []);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
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

export function createHandyRecognizer(config = {}) {
  const env = config.env ?? process.env;
  const handyBin = String(config.handyBin ?? defaultHandyBin(env));
  const spawnImpl = typeof config.spawn === "function" ? config.spawn : spawn;
  const timeoutMs = Number.isSafeInteger(config.timeoutMs) ? config.timeoutMs : 60_000;

  return Object.freeze({
    handyBin,
    async recognize(audio) {
      const bytes = Buffer.isBuffer(audio) ? audio : Buffer.from(audio ?? []);
      if (bytes.length === 0) return "";
      const wavBytes = asWavBytes(bytes);
      if (wavBytes.length <= 44) return "";
      const dir = await mkdtemp(join(tmpdir(), "qq-dictate."));
      const wav = join(dir, "utterance.wav");
      try {
        await writeFile(wav, wavBytes);
        const child = spawnImpl(handyBin, ["--transcribe-file", wav, "--json"], {
          stdio: ["ignore", "pipe", "pipe"],
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
