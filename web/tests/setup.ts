import { mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export default function setup() {
  const directory = fileURLToPath(new URL("./_generated/", import.meta.url));
  mkdirSync(directory, { recursive: true });
  execFileSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      "testsrc2=size=640x360:rate=24",
      "-t",
      "12",
      "-an",
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-g",
      "24",
      "-sc_threshold",
      "0",
      "-f",
      "hls",
      "-hls_time",
      "1",
      "-hls_list_size",
      "0",
      "-hls_playlist_type",
      "event",
      "-hls_segment_filename",
      `${directory}segment-%03d.ts`,
      `${directory}index.m3u8`,
    ],
    { timeout: 30000 },
  );
  execFileSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-i",
      `${directory}index.m3u8`,
      "-frames:v",
      "1",
      `${directory}poster.jpg`,
    ],
    { timeout: 10000 },
  );
}
