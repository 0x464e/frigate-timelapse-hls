import { Buffer } from "node:buffer";
import { execFileSync } from "node:child_process";
import { log } from "node:console";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

const master = fileURLToPath(new URL("./timelapse-icon.png", import.meta.url));
const output = new URL("../public/", import.meta.url);
mkdirSync(output, { recursive: true });

function render(size, opaque = false) {
  const args = ["-hide_banner", "-loglevel", "error", "-i", master];
  if (opaque) {
    args.push(
      "-f",
      "lavfi",
      "-i",
      `color=c=0x111312:s=${size}x${size},format=rgba`,
      "-filter_complex",
      `[0:v]scale=${size}:${size}:flags=lanczos[icon];[1:v][icon]overlay=shortest=1:format=auto,format=rgb24[out]`,
      "-map",
      "[out]",
    );
  } else args.push("-vf", `scale=${size}:${size}:flags=lanczos`);
  args.push("-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1");
  return execFileSync("ffmpeg", args, {
    timeout: 30000,
    maxBuffer: 8 * 1024 * 1024,
  });
}

const images = new Map([16, 32, 48, 256].map((size) => [size, render(size)]));
writeFileSync(new URL("favicon-16x16.png", output), images.get(16));
writeFileSync(new URL("favicon-32x32.png", output), images.get(32));
for (const [name, size, opaque] of [
  ["apple-touch-icon.png", 180, true],
  ["icon-192.png", 192, false],
  ["icon-512.png", 512, false],
  ["icon-maskable-512.png", 512, true],
])
  writeFileSync(new URL(name, output), render(size, opaque));

// ICO directory entries point at complete PNG images, preserving their alpha.
const directory = Buffer.alloc(6 + images.size * 16);
directory.writeUInt16LE(1, 2);
directory.writeUInt16LE(images.size, 4);
let offset = directory.length;
let index = 0;
for (const [size, png] of images) {
  const entry = 6 + index++ * 16;
  directory.writeUInt8(size === 256 ? 0 : size, entry);
  directory.writeUInt8(size === 256 ? 0 : size, entry + 1);
  directory.writeUInt16LE(1, entry + 4);
  directory.writeUInt16LE(32, entry + 6);
  directory.writeUInt32LE(png.length, entry + 8);
  directory.writeUInt32LE(offset, entry + 12);
  offset += png.length;
}
writeFileSync(
  new URL("favicon.ico", output),
  Buffer.concat([directory, ...images.values()]),
);
log("Built favicon, Apple touch icon, and Android standard/maskable icons.");
