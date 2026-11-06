import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Library, Day } from "../src/types";

const directory = fileURLToPath(new URL("./_generated/", import.meta.url));
const videoState = (page: Page) =>
  page.locator("video").evaluate((video) => {
    const element = video as HTMLVideoElement;
    const transform = new DOMMatrix(getComputedStyle(element).transform);
    return {
      paused: element.paused,
      time: element.currentTime,
      rate: element.playbackRate,
      scale: transform.a,
      scaleY: transform.d,
      x: transform.e,
      y: transform.f,
    };
  });
async function pinch(page: Page) {
  const box = (await page.getByTestId("gesture-surface").boundingBox())!;
  const session = await page.context().newCDPSession(page);
  const touches = (spread: number) =>
    [-1, 1].map((side, index) => ({
      id: index + 1,
      x: box.x + box.width / 2 + side * spread,
      y: box.y + box.height * 0.4,
    }));
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: touches(30),
  });
  for (let i = 1; i <= 8; i++) {
    await session.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: touches(30 + 14 * i),
    });
    await page.evaluate(() => new Promise(requestAnimationFrame));
  }
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await session.detach();
}
async function revealMobileControls(page: Page) {
  if (await page.locator(".touch-player").count()) {
    const surface = page.getByTestId("gesture-surface");
    const box = (await surface.boundingBox())!;
    await surface.tap({
      position: { x: box.width * 0.5, y: box.height * 0.25 },
    });
  }
}
const pageErrors = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => {
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(error.message));
});
test.afterEach(({ page }) => {
  expect(pageErrors.get(page)).toEqual([]);
});
const date = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Helsinki",
}).format(new Date());
const makeDay = (value: string): Day => ({
  date: value,
  generation_id: "test-one",
  poster_url: null,
  playlist_url: `/published/test/${value}/index.m3u8`,
  live_playlist_url: `/published/test/${value}/live.m3u8`,
  duration: 12,
  ready: true,
  status: "running",
  mode: "live_follow",
  complete: false,
  stream_ended: false,
  published_at: new Date().toISOString(),
  schedule_start: null,
  schedule_end: null,
  render_speed: 288,
});
async function fixture(page: Page, growing = false, poster = false) {
  let broken = false;
  let failedSegments = 0;
  let segments = growing ? 3 : 12;
  let complete = !growing;
  const day = makeDay(date);
  if (poster) day.poster_url = `/published/test/${date}/poster.jpg`;
  day.duration = segments;
  day.complete = complete;
  day.stream_ended = complete;
  const library: Library = {
    version: 1,
    camera: "test",
    timezone: "Europe/Helsinki",
    schedule: { start: "08:00:00", end: "20:00:00" },
    thumbnail_offset_seconds: 0,
    published_at: new Date().toISOString(),
    days: [
      day,
      { ...makeDay("2026-04-22"), complete: true, stream_ended: true },
    ],
  };
  const media: string[] = [];
  await page.route("**/published/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("library.json"))
      return route.fulfill({ json: library });
    media.push(url.pathname + url.search);
    if (url.pathname.endsWith("poster.jpg"))
      return route.fulfill({
        body: readFileSync(`${directory}poster.jpg`),
        contentType: "image/jpeg",
      });
    if (url.pathname.endsWith(".m3u8")) {
      if (broken)
        return route.fulfill({
          body: "Invalid playlist",
          contentType: "application/vnd.apple.mpegurl",
        });
      const original = readFileSync(`${directory}index.m3u8`, "utf8");
      const lines = original.split("\n");
      let count = 0;
      const output: string[] = [];
      for (const line of lines) {
        if (line === "#EXT-X-ENDLIST") continue;
        if (line.startsWith("#EXTINF:") && count >= segments) break;
        output.push(line);
        if (line.endsWith(".ts")) count++;
      }
      if (complete) output.push("#EXT-X-ENDLIST");
      return route.fulfill({
        body: output.join("\n"),
        contentType: "application/vnd.apple.mpegurl",
      });
    }
    if (url.pathname.endsWith(".ts") && failedSegments > 0) {
      failedSegments--;
      return route.fulfill({ status: 503 });
    }
    if (url.pathname.endsWith(".ts"))
      return route.fulfill({
        body: readFileSync(`${directory}${url.pathname.split("/").at(-1)}`),
        contentType: "video/mp2t",
      });
    return route.fulfill({ status: 404 });
  });
  return {
    media,
    startDay: (value: string, ready = true) => {
      library.days.unshift({
        ...makeDay(value),
        ready,
        duration: ready ? segments : 0,
      });
    },
    interruptSegments: () => {
      failedSegments = 2;
    },
    breakStream: () => {
      broken = true;
    },
    repairStream: () => {
      broken = false;
    },
    regenerate: () => {
      day.generation_id = "test-two";
    },
    grow: () => {
      segments = 7;
      day.duration = 7;
    },
    finish: () => {
      segments = 12;
      day.duration = 12;
      complete = true;
      day.complete = true;
      day.stream_ended = true;
    },
  };
}

test("poster waits for Play; real HLS plays and speed does not persist", async ({
  page,
}) => {
  const { media } = await fixture(page);
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(300);
  expect(media).toHaveLength(0);
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await page.getByLabel("Change playback speed", { exact: true }).click();
  await page.getByLabel("Increase speed by 0.1", { exact: true }).click();
  await expect(
    page.getByRole("slider", { name: "Playback speed", exact: true }),
  ).toHaveAttribute("aria-valuenow", "1.1");
  await page.keyboard.press("Escape");
  await page.getByLabel("Player settings", { exact: true }).click();
  await page.getByLabel("Skip forward / back").fill("9");
  await page.getByLabel("Close settings").click();
  await page.goto("/days/2026-04-22");
  await page.getByLabel("Player settings", { exact: true }).click();
  await expect(page.getByLabel("Skip forward / back")).toHaveValue("9");
  await page.getByLabel("Close settings").click();
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await page.getByLabel("Change playback speed", { exact: true }).click();
  await expect(
    page.getByRole("slider", { name: "Playback speed", exact: true }),
  ).toHaveAttribute("aria-valuenow", "1");
});

test("calendar navigates months and day links remain paused", async ({
  page,
}) => {
  const { media } = await fixture(page);
  await page.goto("/archive?month=2026-04");
  await expect(page.getByRole("heading", { name: "April 2026" })).toBeVisible();
  await page.locator(".day-card").click();
  await expect(page).toHaveURL(/\/days\/2026-04-22$/);
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
  expect(media).toHaveLength(0);
});

test("Today opens today after playing a historical day through SPA navigation", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/archive?month=2026-04");
  await page.locator(".day-card").click();
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await page
    .getByRole("link", { name: "Timelapse today", exact: true })
    .click();
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${date}`,
  );
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
});

test("timeline has a large hit target and commits a tap seek", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).readyState),
    )
    .toBeGreaterThan(1);
  const slider = page.getByRole("slider", { name: "Seek timeline" });
  const box = await slider.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(48);
  await slider.click({ position: { x: box!.width * 0.5, y: box!.height / 2 } });
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(4);
});

test("a growing event extends without replacing its video instance", async ({
  page,
}) => {
  const { grow, finish } = await fixture(page, true);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await page
    .locator("video")
    .evaluate((video) => (video.dataset.instance = "original"));
  await page.getByLabel("Change playback speed", { exact: true }).click();
  await page.getByLabel("Increase speed by 0.1", { exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(
    page.getByText("Waiting for more footage", { exact: true }),
  ).toBeVisible();
  grow();
  await expect
    .poll(
      () =>
        page
          .locator("video")
          .evaluate((video) => (video as HTMLVideoElement).duration),
      { timeout: 15000 },
    )
    .toBeGreaterThan(5);
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(3);
  await expect(page.locator("video")).toHaveAttribute(
    "data-instance",
    "original",
  );
  finish();
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect
    .poll(
      () =>
        page
          .locator("video")
          .evaluate((video) => (video as HTMLVideoElement).duration),
      { timeout: 15000 },
    )
    .toBeGreaterThan(10);
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).playbackRate),
    )
    .toBe(1.1);
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(3);
  expect(
    await page
      .locator("video")
      .evaluate((v) => (v as HTMLVideoElement).currentTime),
  ).toBeLessThan(9);
  await page
    .getByRole("button", { name: "Jump to latest available footage" })
    .click();
  await expect
    .poll(() =>
      page.locator("video").evaluate((v) => (v as HTMLVideoElement).ended),
    )
    .toBe(true);
  await expect(
    page.getByText("Waiting for more footage", { exact: true }),
  ).toHaveCount(0);
});

test("hold speed is temporary and cancellation restores the rate", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  const surface = page.getByTestId("gesture-surface");
  const box = (await surface.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.4);
  await page.mouse.down();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).playbackRate),
    )
    .toBe(2);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await page.mouse.up();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video) => (video as HTMLVideoElement).playbackRate),
    )
    .toBe(1);
});

test("double tap seeks without boosting and explicit Resume is separate from Play", async ({
  page,
  isMobile,
}) => {
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  const surface = page.getByTestId("gesture-surface");
  const box = (await surface.boundingBox())!;
  const point = { x: box.width * 0.8, y: box.height * 0.4 };
  if (isMobile) {
    await surface.tap({ position: point });
    await surface.tap({ position: point });
  } else await surface.dblclick({ position: point, delay: 70 });
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(4);
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).playbackRate),
    )
    .toBe(1);
  await page.reload();
  await page.getByRole("button", { name: /^Resume from/ }).click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(4);
  await page.reload();
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  expect(
    await page
      .locator("video")
      .evaluate((v) => (v as HTMLVideoElement).currentTime),
  ).toBeLessThan(3);
});

test("new segments preserve a user pause", async ({ page }) => {
  const stream = await fixture(page, true);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await revealMobileControls(page);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const at = await page
    .locator("video")
    .evaluate((v) => (v as HTMLVideoElement).currentTime);
  stream.grow();
  await expect
    .poll(
      () =>
        page.locator("video").evaluate((v) => (v as HTMLVideoElement).duration),
      { timeout: 15000 },
    )
    .toBeGreaterThan(5);
  expect(
    await page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
  ).toBe(true);
  expect(
    await page
      .locator("video")
      .evaluate((v) => (v as HTMLVideoElement).currentTime),
  ).toBeCloseTo(at, 1);
});

test("segment downloads recover after a short network interruption", async ({
  page,
}) => {
  const stream = await fixture(page, true);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  stream.interruptSegments();
  stream.grow();
  await expect
    .poll(
      () =>
        page
          .locator("video")
          .evaluate((v) => (v as HTMLVideoElement).currentTime),
      { timeout: 15000 },
    )
    .toBeGreaterThan(3);
  await expect(
    page.getByRole("button", { name: "Retry playback" }),
  ).toHaveCount(0);
});

test("a failed stream can retry and regeneration returns to the poster", async ({
  page,
}) => {
  const stream = await fixture(page);
  stream.breakStream();
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry playback" }),
  ).toBeVisible();
  stream.repairStream();
  await page.getByRole("button", { name: "Retry playback" }).click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  stream.regenerate();
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
  await expect(page.locator("video")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() => stream.media.some((url) => url.includes("generation=test-two")))
    .toBe(true);
});

test("unsupported fullscreen, PiP and wake lock keep playback functional", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(document, "pictureInPictureEnabled", {
      value: false,
      configurable: true,
    });
    Object.defineProperty(navigator, "wakeLock", {
      value: undefined,
      configurable: true,
    });
    Element.prototype.requestFullscreen = () =>
      Promise.reject(new Error("Unavailable"));
  });
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await expect(
    page.getByRole("button", { name: "Picture in picture" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Enter fullscreen" }).click();
  await expect(
    page.getByText("Fullscreen is unavailable in this browser"),
  ).toBeVisible();
  expect(
    await page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
  ).toBe(false);
});

test("landing opens the prior day before today's session starts; explicit dates stay explicit", async ({
  page,
}) => {
  const next = new Date(`${date}T12:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  const nextDate = next.toISOString().slice(0, 10);
  await page.clock.install({ time: new Date(`${nextDate}T00:15:00+02:00`) });
  const { media } = await fixture(page);
  await page.goto("/");
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${date}`,
  );
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
  expect(media).toHaveLength(0);
  await page.goto(`/days/${nextDate}`);
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${nextDate}`,
  );
  await expect(page.getByText("The day begins at 08:00")).toBeVisible();
  expect(media).toHaveLength(0);
});

test("default day follows new sessions until playback pins the open day", async ({
  page,
}) => {
  const next = new Date(`${date}T12:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  const nextDate = next.toISOString().slice(0, 10);
  await page.clock.install({ time: new Date(`${date}T21:59:50Z`) });
  const { media, startDay } = await fixture(page);
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
  await page.clock.fastForward(20000);
  await expect(
    page.getByRole("button", { name: "Play timelapse", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${date}`,
  );
  startDay(nextDate, false);
  await page.clock.setSystemTime(new Date(`${nextDate}T08:00:00+02:00`));
  await page.clock.fastForward(10000);
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${nextDate}`,
  );
  await expect(
    page.getByText("Waiting for the first encoded segment"),
  ).toBeVisible();
  expect(media).toHaveLength(0);
  await page.clock.setSystemTime(new Date(`${date}T21:59:50Z`));
  await page.reload();
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await page.clock.fastForward(20000);
  await expect(page.locator("video")).toHaveCount(1);
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${date}`,
  );
  await page.clock.setSystemTime(new Date(`${nextDate}T08:00:00+02:00`));
  await page.clock.fastForward(10000);
  await expect(page.locator(".player-shell")).toHaveAttribute(
    "aria-label",
    `test timelapse for ${date}`,
  );
});

test("speed adjusts in a small non-modal panel while video keeps playing", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await page.getByLabel("Change playback speed", { exact: true }).click();
  const panel = page.getByRole("dialog", {
    name: "Playback speed",
    exact: true,
  });
  await expect(panel).toHaveAttribute("aria-modal", "false");
  const box = (await panel.boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(300);
  expect(box.height).toBeLessThan(150);
  await expect(page.locator(".dialog-overlay")).toHaveCount(0);
  await page.getByLabel("Increase speed by 0.1").click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).playbackRate),
    )
    .toBe(1.1);
  expect(
    await page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
  ).toBe(false);
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(
    page.getByLabel("Change playback speed", { exact: true }),
  ).toBeFocused();
  await page.getByLabel("Change playback speed", { exact: true }).click();
  await page.getByRole("button", { name: "Start over" }).click();
  await expect(panel).toHaveCount(0);
});

test("single taps toggle playback and double taps seek without toggling it", async ({
  page,
  isMobile,
}) => {
  test.skip(
    isMobile,
    "Mobile taps reveal the center transport instead of toggling playback",
  );
  await fixture(page);
  await page.goto("/");
  const surface = page.getByTestId("gesture-surface");
  const rect = (await surface.boundingBox())!;
  const center = { x: rect.width * 0.5, y: rect.height * 0.5 };
  const side = { x: rect.width * 0.8, y: rect.height * 0.5 };
  const tap = (point: { x: number; y: number }) =>
    isMobile
      ? surface.tap({ position: point })
      : surface.click({ position: point });
  await tap(center);
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await tap(center);
  await expect
    .poll(() =>
      page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
    )
    .toBe(true);
  if (isMobile) {
    await tap(side);
    await tap(side);
  } else await surface.dblclick({ position: side, delay: 70 });
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(4);
  expect(
    await page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
  ).toBe(true);
  await tap(side);
  await expect
    .poll(() =>
      page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
    )
    .toBe(false);
  await tap(side);
  await expect
    .poll(() =>
      page.locator("video").evaluate((v) => (v as HTMLVideoElement).paused),
    )
    .toBe(true);
});

test("portrait PiP stays visible and landscape controls use only two compact rows", async ({
  page,
}) => {
  await page.setViewportSize({ width: 393, height: 851 });
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((v) => (v as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await revealMobileControls(page);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Picture in picture" }),
  ).toBeVisible();
  const settings = (await page
    .getByLabel("Player settings", { exact: true })
    .boundingBox())!;
  const archive = (await page
    .getByRole("link", { name: "Archive", exact: true })
    .boundingBox())!;
  expect(
    Math.abs(settings.y + settings.height / 2 - archive.y - archive.height / 2),
  ).toBeLessThan(1);
  expect(settings.x).toBeGreaterThan(archive.x);
  await expect(page.getByLabel(/(?:Previous|Next) available day/)).toHaveCount(
    0,
  );
  await expect(page.locator(".bottom-meta, .day-heading")).toHaveCount(0);
  await page.setViewportSize({ width: 851, height: 393 });
  const controls = (await page.locator(".player-bottom").boundingBox())!;
  expect(controls.height).toBeLessThanOrEqual(100);
  expect(
    (await page.getByRole("slider", { name: "Seek timeline" }).boundingBox())!
      .height,
  ).toBeGreaterThanOrEqual(48);
});

test("loading status is centered over the player", async ({ page }) => {
  await fixture(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/*.m3u8*", async (route) => {
    await gate;
    await route.fallback();
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  const status = page.getByText("Loading your timelapse", { exact: true });
  try {
    await expect(status).toBeVisible();
    const message = (await status.boundingBox())!;
    const player = (await page.locator(".media-stage").boundingBox())!;
    expect(
      Math.abs(message.y + message.height / 2 - player.y - player.height / 2),
    ).toBeLessThan(1);
  } finally {
    release();
  }
});

test("mobile taps toggle portrait transport while playing or paused; Play fades quickly", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "Mobile-only controls");
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(async () => (await videoState(page)).time)
    .toBeGreaterThan(0);
  await expect(page.locator(".center-play")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  await expect(page.locator(".player-bottom")).toHaveCSS("opacity", "1");
  await revealMobileControls(page);
  expect((await videoState(page)).paused).toBe(false);
  await expect(page.locator(".center-play")).toHaveAttribute(
    "aria-hidden",
    "false",
  );
  await revealMobileControls(page);
  await expect(page.locator(".center-play")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  expect((await videoState(page)).paused).toBe(false);
  await expect(page.locator(".player-bottom")).toHaveCSS("opacity", "1");
  await revealMobileControls(page);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect.poll(async () => (await videoState(page)).paused).toBe(true);
  await revealMobileControls(page);
  await expect(page.locator(".center-play")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  expect((await videoState(page)).paused).toBe(true);
  await expect(page.locator(".player-bottom")).toHaveCSS("opacity", "1");
  await revealMobileControls(page);
  await page.waitForTimeout(3000);
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.locator(".center-play")).toHaveAttribute(
    "aria-hidden",
    "true",
    { timeout: 1500 },
  );
  await expect(page.locator(".player-bottom")).toHaveCSS("opacity", "1");
  await expect(page.locator(".watch-nav")).toHaveCSS("opacity", "1");
  const stage = (await page.locator(".media-stage").boundingBox())!;
  const controls = (await page.locator(".player-bottom").boundingBox())!;
  expect(stage.y + stage.height).toBeLessThanOrEqual(controls.y + 1);
});

test("portrait landing text and Resume fit inside and center on the poster", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "Portrait mobile layout");
  const { media } = await fixture(page, false, true);
  await page.addInitScript(
    ({ date }) => {
      localStorage.setItem(`timelapse.position.test.${date}.test-one`, "5");
    },
    { date },
  );
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /^Resume from/ }),
  ).toBeVisible();
  await page
    .locator(".poster")
    .evaluate((image) => (image as HTMLImageElement).decode());
  for (const size of [
    { width: 393, height: 851 },
    { width: 393, height: 740 },
    { width: 320, height: 720 },
  ]) {
    await page.setViewportSize(size);
    const geometry = await page.locator(".poster").evaluate((image) => {
      const poster = image as HTMLImageElement;
      const box = poster.getBoundingClientRect();
      const fit = Math.min(
        box.width / poster.naturalWidth,
        box.height / poster.naturalHeight,
      );
      const content = document
        .querySelector(".intro-content")!
        .getBoundingClientRect();
      return {
        center: box.y + box.height / 2,
        height: poster.naturalHeight * fit,
        contentTop: content.top,
        contentBottom: content.bottom,
      };
    });
    expect(
      Math.abs(
        (geometry.contentTop + geometry.contentBottom) / 2 - geometry.center,
      ),
    ).toBeLessThan(1);
    expect(geometry.contentTop).toBeGreaterThanOrEqual(
      geometry.center - geometry.height / 2 - 1,
    );
    expect(geometry.contentBottom).toBeLessThanOrEqual(
      geometry.center + geometry.height / 2 + 1,
    );
  }
  expect(media.some((url) => /\.(m3u8|ts)(?:\?|$)/.test(url))).toBe(false);
});

test("paused mobile landscape can hide and restore the whole interface", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "Mobile-only controls");
  await page.setViewportSize({ width: 851, height: 393 });
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(async () => (await videoState(page)).time)
    .toBeGreaterThan(0);
  await revealMobileControls(page);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await revealMobileControls(page);
  await expect(page.locator(".player-bottom")).toHaveCSS("opacity", "0");
  await expect(page.locator(".watch-nav")).toHaveCSS("opacity", "0");
  await expect(page.locator(".center-play")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  expect((await videoState(page)).paused).toBe(true);
  await revealMobileControls(page);
  await expect(page.locator(".player-bottom")).toHaveCSS("opacity", "1");
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toBeVisible();
});

test("mobile pinch fills portrait, pans without seeking, and resets from the bottom controls", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "Mobile-only zoom");
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(async () => (await videoState(page)).time)
    .toBeGreaterThan(0);
  await revealMobileControls(page);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const at = (await videoState(page)).time;
  await pinch(page);
  const zoomed = await videoState(page);
  expect(zoomed.scale).toBeGreaterThan(3);
  expect(zoomed.scale).toBeCloseTo(zoomed.scaleY);
  expect(zoomed.paused).toBe(true);
  const box = (await page.getByTestId("gesture-surface").boundingBox())!;
  expect((box.width / (16 / 9)) * zoomed.scale).toBeGreaterThanOrEqual(
    box.height,
  );
  const session = await page.context().newCDPSession(page);
  const point = {
    id: 1,
    x: box.x + box.width * 0.25,
    y: box.y + box.height * 0.25,
  };
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [point],
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ ...point, x: point.x + 70, y: point.y - 70 }],
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  const panned = await videoState(page);
  expect(panned.x).not.toBeCloseTo(zoomed.x);
  expect(panned.time).toBeCloseTo(at, 1);
  const surface = page.getByTestId("gesture-surface");
  await surface.tap({ position: { x: box.width * 0.8, y: box.height * 0.4 } });
  await surface.tap({ position: { x: box.width * 0.8, y: box.height * 0.4 } });
  await expect
    .poll(async () => (await videoState(page)).time)
    .toBeGreaterThan(4);
  expect((await videoState(page)).paused).toBe(true);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [point],
  });
  await page.waitForTimeout(650);
  expect((await videoState(page)).rate).toBe(1);
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await session.detach();
  await page
    .locator(".player-bottom")
    .getByRole("button", { name: "Reset zoom" })
    .click();
  await expect.poll(async () => (await videoState(page)).scale).toBe(1);
  expect((await videoState(page)).x).toBe(0);
  await expect(page.getByRole("button", { name: "Reset zoom" })).toHaveCount(0);
});

test("desktop keeps click-to-pause and has no custom zoom", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "Desktop behavior");
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "Play timelapse", exact: true })
    .click();
  await expect
    .poll(async () => (await videoState(page)).time)
    .toBeGreaterThan(0);
  await pinch(page);
  expect((await videoState(page)).scale).toBe(1);
  await expect(page.getByRole("button", { name: "Reset zoom" })).toHaveCount(0);
  await expect(page.locator(".center-play")).toHaveCount(0);
});
