import { Container, PlayButton } from "@videojs/react";
import { Video, VideoPlayer } from "@videojs/react/video";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowLeft,
  CalendarDays,
  Camera,
  CircleHelp,
  FastForward,
  Film,
  Maximize,
  Minimize,
  Pause,
  PictureInPicture2,
  Play,
  Settings,
  ZoomOut,
  SkipBack,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Link } from "react-router-dom";
import {
  clamp,
  emptyState,
  formatTime,
  getSeekInterval,
  getStored,
  prettyDate,
  resumeKey,
  setStored,
  todayIn,
} from "../lib";
import type { Day, Library } from "../types";
import { SpeedPopover } from "./SpeedPopover";
import { Timeline } from "./Timeline";
import { useMediaQuery } from "../useMediaQuery";
import { useVideoZoom } from "../useVideoZoom";
import { GESTURE_SLOP } from "../zoom";

interface Props {
  library: Library;
  date: string;
  day?: Day;
  now: Date;
  onStart: () => void;
}
type WakeSentinel = { release: () => Promise<void> };
type OrientationLock = ScreenOrientation & {
  lock?: (orientation: string) => Promise<void>;
};

export function Player({ library, day, date, now, onStart }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const shell = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const desiredPlayback = useRef(false);
  const initialPosition = useRef(0);
  const position = useRef(0);
  const [started, setStarted] = useState(false);
  const [paused, setPaused] = useState(true);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [time, setTime] = useState(0);
  const [mediaEnd, setMediaEnd] = useState(0);
  const [buffered, setBuffered] = useState<[number, number][]>([]);
  const [speed, setSpeed] = useState(10);
  const [boosting, setBoosting] = useState(false);
  const selectedRate = useRef(1);
  const loadingSource = useRef(false);
  const [seekStep, setSeekStep] = useState(getSeekInterval);
  const [seekDraft, setSeekDraft] = useState(() => String(seekStep));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [speedOpen, setSpeedOpen] = useState(false);
  const [controls, setControls] = useState(true);
  const [centerRequested, setCenterRequested] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [full, setFull] = useState(false);
  const [pip, setPip] = useState(false);
  const [notice, setNotice] = useState("");
  const [posterFailed, setPosterFailed] = useState(false);
  const mobile = useMediaQuery("(pointer: coarse)");
  const portrait = useMediaQuery("(orientation: portrait)");
  const zoom = useVideoZoom(mobile && started, stage, video);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const holdTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const tapTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const gesture = useRef<{
    x: number;
    y: number;
    start: number;
    held: boolean;
    moved: boolean;
  } | null>(null);
  const lastTap = useRef<{ at: number; side: string } | null>(null);
  const activeToday = date === todayIn(library.timezone, now);
  const isGrowing = activeToday && !day?.complete;
  const duration = started && mediaEnd > 0 ? mediaEnd : (day?.duration ?? 0);
  const saved = day ? Number(getStored(resumeKey(library.camera, day))) : 0;
  const hasResume =
    Number.isFinite(saved) && saved > 1 && saved < (day?.duration ?? 0) - 0.5;
  const sourceBase = day
    ? isGrowing
      ? day.live_playlist_url
      : day.playlist_url
    : "";
  const source = sourceBase
    ? `${sourceBase}${sourceBase.includes("?") ? "&" : "?"}generation=${encodeURIComponent(day!.generation_id)}`
    : "";
  const sourceMode = day?.stream_ended ? "ended" : "growing";
  const savedKey = day ? resumeKey(library.camera, day) : "";

  const announce = useCallback((message: string) => {
    setNotice(message);
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(""), 1200);
  }, []);
  const seek = useCallback(
    (target: number) => {
      if (video.current) {
        video.current.currentTime = clamp(target, 0, duration);
        setTime(video.current.currentTime);
      }
    },
    [duration],
  );
  const showControls = useCallback((delay = 2800) => {
    setControls(true);
    clearTimeout(idleTimer.current);
    if (desiredPlayback.current)
      idleTimer.current = setTimeout(() => setControls(false), delay);
  }, []);
  useEffect(() => {
    showControls();
  }, [mobile, portrait, showControls]);
  useLayoutEffect(() => {
    if (!mobile || !started || !shell.current) return;
    const root = shell.current;
    const nav = root.querySelector<HTMLElement>(".watch-nav")!;
    const bottom = root.querySelector<HTMLElement>(".player-bottom")!;
    const measure = () => {
      root.style.setProperty("--header-height", `${nav.offsetHeight}px`);
      root.style.setProperty("--controls-height", `${bottom.offsetHeight}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    observer.observe(bottom);
    return () => observer.disconnect();
  }, [mobile, started]);
  const showSpeed = useCallback(
    (open: boolean) => {
      setSpeedOpen(open);
      showControls();
    },
    [showControls],
  );
  const start = (at = 0) => {
    initialPosition.current = at;
    position.current = at;
    desiredPlayback.current = true;
    onStart();
    setStarted(true);
    setWaiting(true);
    setPaused(false);
    showControls(mobile ? 600 : 2800);
  };
  const toggle = () => {
    const element = video.current;
    if (!element) return;
    if (!desiredPlayback.current || element.paused) {
      desiredPlayback.current = true;
      void element.play().catch(() => setPaused(true));
    } else {
      desiredPlayback.current = false;
      element.pause();
      setPaused(true);
    }
    showControls(mobile ? 600 : 2800);
  };
  const cancelBoost = useCallback(() => {
    clearTimeout(holdTimer.current);
    setBoosting(false);
  }, []);

  useEffect(() => {
    const element = video.current;
    if (!started || !element || !source) return;
    let cancelled = false;
    let destroy = () => {};
    let recoveries = 0;
    let manifestLoaded = false;
    loadingSource.current = true;
    const begin = () => {
      if (cancelled) return;
      element.playbackRate = selectedRate.current;
      if (initialPosition.current > 0) {
        element.currentTime = initialPosition.current;
        initialPosition.current = 0;
      }
      if (desiredPlayback.current)
        void element.play().catch(() => {
          if (!cancelled) {
            setPaused(true);
            setWaiting(false);
          }
        });
    };
    setError("");
    setWaiting(true);
    void import("hls.js")
      .then(({ default: Hls }) => {
        if (cancelled) return;
        if (!Hls.isSupported()) {
          if (!element.canPlayType("application/vnd.apple.mpegurl")) {
            setError("This browser cannot play HLS video.");
            return;
          }
          element.addEventListener("loadedmetadata", begin, { once: true });
          const nativeError = () =>
            setError("Playback interrupted. Please retry.");
          element.addEventListener("error", nativeError);
          destroy = () => {
            element.removeEventListener("loadedmetadata", begin);
            element.removeEventListener("error", nativeError);
          };
          element.src = source;
          element.load();
          return;
        }
        const hls = new Hls({
          startPosition: position.current,
          maxLiveSyncPlaybackRate: 1,
          liveMaxLatencyDurationCount: Infinity,
          liveSyncDurationCount: 1,
          liveDurationInfinity: false,
          backBufferLength: 60,
          // Version segment requests too: regenerated filenames can be reused.
          xhrSetup: (xhr, url) => {
            const requestUrl = new URL(url, window.location.href);
            requestUrl.searchParams.set(
              "generation",
              new URL(source, window.location.href).searchParams.get(
                "generation",
              )!,
            );
            xhr.open("GET", requestUrl.href, true);
          },
        });
        destroy = () => hls.destroy();
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          manifestLoaded = true;
          begin();
        });
        hls.on(Hls.Events.ERROR, (_, data) => {
          if (!data.fatal || cancelled) return;
          if (
            manifestLoaded &&
            data.type === Hls.ErrorTypes.NETWORK_ERROR &&
            recoveries++ < 3
          ) {
            hls.startLoad(position.current);
          } else if (
            data.type === Hls.ErrorTypes.MEDIA_ERROR &&
            recoveries++ < 3
          ) {
            hls.recoverMediaError();
          } else {
            setError("Playback interrupted. Your position is saved.");
            setWaiting(false);
          }
        });
        hls.loadSource(source);
        hls.attachMedia(element);
      })
      .catch(() => {
        if (!cancelled)
          setError("The player could not be loaded. Please retry.");
      });
    return () => {
      cancelled = true;
      loadingSource.current = true;
      initialPosition.current = position.current;
      destroy();
      element.pause();
      element.removeAttribute("src");
      element.load();
    };
  }, [started, source, sourceMode, retry]);

  useEffect(() => {
    selectedRate.current = (boosting ? Math.min(speed * 2, 40) : speed) / 10;
    if (video.current) video.current.playbackRate = selectedRate.current;
  }, [speed, boosting, started]);
  useEffect(() => {
    const lost = () => {
      cancelBoost();
      clearTimeout(tapTimer.current);
      lastTap.current = null;
      gesture.current = null;
    };
    window.addEventListener("blur", lost);
    document.addEventListener("visibilitychange", lost);
    return () => {
      window.removeEventListener("blur", lost);
      document.removeEventListener("visibilitychange", lost);
    };
  }, [cancelBoost]);
  useEffect(() => {
    let sentinel: WakeSentinel | undefined;
    let disposed = false;
    const release = () => {
      void sentinel?.release().catch(() => {});
      sentinel = undefined;
    };
    const acquire = async () => {
      release();
      if (
        paused ||
        waiting ||
        !started ||
        document.hidden ||
        !("wakeLock" in navigator)
      )
        return;
      try {
        const acquired = await navigator.wakeLock.request("screen");
        if (disposed || document.hidden) await acquired.release();
        else sentinel = acquired;
      } catch {
        /* HTTP, battery policy, and platform restrictions are expected. */
      }
    };
    void acquire();
    document.addEventListener("visibilitychange", acquire);
    return () => {
      disposed = true;
      release();
      document.removeEventListener("visibilitychange", acquire);
    };
  }, [paused, waiting, started]);
  useEffect(() => {
    setPip(Boolean(document.pictureInPictureEnabled));
    const changed = () => {
      const entered = document.fullscreenElement === shell.current;
      setFull(entered);
      if (!entered) {
        try {
          screen.orientation?.unlock();
        } catch {
          /* unsupported */
        }
      }
    };
    document.addEventListener("fullscreenchange", changed);
    return () => {
      document.removeEventListener("fullscreenchange", changed);
      try {
        screen.orientation?.unlock();
      } catch {
        /* unsupported */
      }
    };
  }, []);
  useEffect(
    () => () => {
      clearTimeout(idleTimer.current);
      clearTimeout(noticeTimer.current);
      clearTimeout(holdTimer.current);
      clearTimeout(tapTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (savedKey && time > 0) setStored(savedKey, String(time));
  }, [time, savedKey]);

  const fullscreen = async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        return;
      }
      if (shell.current?.requestFullscreen) {
        await shell.current.requestFullscreen();
        try {
          await (screen.orientation as OrientationLock)?.lock?.("landscape");
        } catch {
          /* no orientation locking */
        }
      } else {
        const native = video.current as
          (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
        native?.webkitEnterFullscreen?.();
      }
    } catch {
      announce("Fullscreen is unavailable in this browser");
    }
  };
  const measure = () => {
    const element = video.current;
    if (!element) return;
    const seekable = element.seekable;
    const end = seekable.length
      ? seekable.end(seekable.length - 1)
      : element.duration;
    if (Number.isFinite(end) && end > 0) setMediaEnd(end);
    const ranges: [number, number][] = [];
    for (let i = 0; i < element.buffered.length; i++)
      ranges.push([element.buffered.start(i), element.buffered.end(i)]);
    setBuffered(ranges);
    // A stopped finite stream can later resume. Never resume a user-paused video.
    if (
      desiredPlayback.current &&
      element.ended &&
      isGrowing &&
      end > element.currentTime + 0.1
    ) {
      void element.play().catch(() => {});
    }
  };
  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!day?.ready || settingsOpen || event.button !== 0) return;
    clearTimeout(tapTimer.current);
    if (zoom.down(event)) {
      cancelBoost();
      lastTap.current = null;
      gesture.current = null;
      return;
    }
    if (!mobile) showControls();
    gesture.current = {
      x: event.clientX,
      y: event.clientY,
      start: performance.now(),
      held: false,
      moved: false,
    };
    clearTimeout(holdTimer.current);
    if (!zoom.isZoomed())
      holdTimer.current = setTimeout(() => {
        if (!gesture.current || gesture.current.moved) return;
        gesture.current.held = true;
        lastTap.current = null;
        if (started && desiredPlayback.current && speed < 40) setBoosting(true);
      }, 450);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const current = gesture.current;
    gesture.current = null;
    cancelBoost();
    if (zoom.up(event)) return;
    if (!current || current.held || current.moved) return;
    if (!started) {
      if (!mobile) start();
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const side =
      event.clientX - rect.left < rect.width * 0.4
        ? "left"
        : event.clientX - rect.left > rect.width * 0.6
          ? "right"
          : "center";
    const at = performance.now();
    const tap = () => {
      if (!mobile) {
        toggle();
        return;
      }
      if (portrait) {
        setCenterRequested(!(controls && centerRequested));
        showControls();
        return;
      }
      if (!portrait && !desiredPlayback.current) {
        clearTimeout(idleTimer.current);
        setControls((value) => !value);
      } else showControls();
      setCenterRequested(true);
    };
    if (
      lastTap.current &&
      at - lastTap.current.at < 320 &&
      side === lastTap.current.side &&
      side !== "center"
    ) {
      const amount = side === "left" ? -seekStep : seekStep;
      seek((video.current?.currentTime ?? 0) + amount);
      announce(`${amount > 0 ? "+" : "−"}${Math.abs(amount)} seconds`);
      lastTap.current = null;
    } else {
      lastTap.current = { at, side };
      // Give a side tap time to become a double-tap seek without pausing first.
      if (side === "center") {
        lastTap.current = null;
        tap();
      } else {
        tapTimer.current = setTimeout(() => {
          lastTap.current = null;
          tap();
        }, 320);
      }
    }
  };
  const visibleControls =
    controls ||
    (mobile ? portrait : paused) ||
    settingsOpen ||
    speedOpen ||
    scrubbing ||
    !started ||
    Boolean(error);
  const centerVisible =
    controls && centerRequested && !settingsOpen && !speedOpen && !error;
  return (
    <VideoPlayer>
      <Container
        ref={shell}
        className={`player-shell ${started ? "started" : ""} ${mobile ? "touch-player" : ""} ${portrait ? "portrait-player" : ""} ${visibleControls ? "" : "controls-hidden"}`}
        tabIndex={0}
        aria-label={`${library.camera} timelapse for ${date}`}
        onPointerDownCapture={(event) => {
          if (
            (event.target as HTMLElement).closest(
              "button, a, input, [role='slider'], [role='dialog']",
            )
          ) {
            clearTimeout(tapTimer.current);
            lastTap.current = null;
            showControls();
            if (!(event.target as HTMLElement).closest(".center-play"))
              setCenterRequested(false);
          }
        }}
        onPointerMove={(event) => {
          if (event.pointerType === "mouse") showControls();
        }}
        onFocusCapture={(event) => {
          // Touch-generated focus arrives after pointerup; it must not undo hiding the UI.
          if ((event.target as HTMLElement).matches(":focus-visible"))
            showControls();
        }}
        onKeyDown={(event) => {
          if (
            settingsOpen ||
            (event.target as HTMLElement).closest(
              'button, a, input, [role="slider"], [role="dialog"]',
            )
          )
            return;
          if (["ArrowLeft", "ArrowRight", " ", "k", "f"].includes(event.key)) {
            event.preventDefault();
            showControls();
            if (!started) {
              if (event.key === " " && day?.ready) start();
              return;
            }
            if (event.key === "ArrowLeft" || event.key === "ArrowRight")
              seek(time + (event.key === "ArrowRight" ? seekStep : -seekStep));
            else if (event.key === "f") void fullscreen();
            else toggle();
          }
        }}
      >
        <div
          ref={stage}
          className={`media-stage ${mobile && centerVisible ? "center-controls-visible" : ""}`}
        >
          {day?.poster_url && !posterFailed && !started && (
            <img
              className="poster"
              src={day.poster_url}
              alt=""
              onError={() => setPosterFailed(true)}
            />
          )}
          {started && (
            <Video
              ref={video}
              className="video"
              style={zoom.style}
              playsInline
              muted
              preload="none"
              onTimeUpdate={() => {
                const value = video.current?.currentTime ?? 0;
                position.current = value;
                setTime(value);
              }}
              onDurationChange={measure}
              onProgress={measure}
              onLoadedMetadata={() => {
                measure();
                zoom.measure();
              }}
              onPlay={() => {
                desiredPlayback.current = true;
                setPaused(false);
                setCenterRequested(false);
                showControls(mobile ? 600 : 2800);
              }}
              onPause={() => {
                // Native PiP/media-key pauses must remain pauses when new footage arrives.
                if (!loadingSource.current && !video.current?.ended)
                  desiredPlayback.current = false;
                setPaused(true);
                setCenterRequested(true);
                cancelBoost();
                showControls();
              }}
              onPlaying={() => {
                loadingSource.current = false;
                setWaiting(false);
                setPaused(false);
              }}
              onWaiting={() => setWaiting(true)}
              onEnded={() => {
                if (isGrowing && !day?.complete && !day?.stream_ended)
                  setWaiting(true);
                else {
                  setPaused(true);
                  if (day?.complete || !activeToday)
                    desiredPlayback.current = false;
                  showControls();
                }
              }}
            />
          )}
          <div
            className="gesture-surface"
            data-testid="gesture-surface"
            onPointerDown={pointerDown}
            onPointerMove={(event) => {
              if (zoom.move(event)) {
                gesture.current = null;
                lastTap.current = null;
                clearTimeout(tapTimer.current);
                cancelBoost();
                return;
              }
              const current = gesture.current;
              if (
                current &&
                Math.hypot(
                  event.clientX - current.x,
                  event.clientY - current.y,
                ) > GESTURE_SLOP
              ) {
                current.moved = true;
                cancelBoost();
              }
            }}
            onPointerUp={pointerUp}
            onPointerCancel={(event) => {
              zoom.up(event);
              gesture.current = null;
              cancelBoost();
            }}
            onLostPointerCapture={(event) => {
              zoom.up(event);
              gesture.current = null;
              cancelBoost();
            }}
            onContextMenu={(event) => event.preventDefault()}
          />
          {!started && (
            <div className="intro">
              <div className="intro-content">
                {!day?.poster_url || posterFailed ? (
                  <Camera
                    className="placeholder-icon"
                    size={42}
                    strokeWidth={1}
                  />
                ) : null}
                <span className="eyebrow">
                  {library.camera.replaceAll("_", " ")}{" "}
                  <span className="small-dot" />{" "}
                  {activeToday ? "TODAY, SO FAR" : "FROM THE ARCHIVE"}
                </span>
                <h1>{prettyDate(date, true)}</h1>
                {day?.ready ? (
                  <>
                    <p>
                      {formatTime(day.duration)} of the day, ready to explore.
                    </p>
                    <button
                      className="hero-play"
                      onClick={() => start()}
                      aria-label="Play timelapse"
                    >
                      <Play size={28} fill="currentColor" />{" "}
                      <span>Play the day</span>
                    </button>
                    {hasResume && (
                      <button
                        className="resume-button"
                        onClick={() => start(clamp(saved, 0, day.duration))}
                      >
                        Resume from {formatTime(saved)}{" "}
                        <ArrowLeft className="resume-arrow" size={14} />
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    <p className="empty-heading" role="status">
                      {emptyState(library, date, day, now)}
                    </p>
                    <span className="intro-note">
                      The player will be ready when footage is available.
                    </span>
                  </>
                )}
              </div>
            </div>
          )}
          {boosting && (
            <div className="boost-badge">
              <FastForward size={17} />{" "}
              {(Math.min(speed * 2, 40) / 10).toFixed(1)}×
            </div>
          )}
          {notice && (
            <div className="notice" role="status">
              {notice}
            </div>
          )}
          {started && waiting && !paused && !error && (
            <div className="waiting-badge" role="status">
              <span className="pulse-dot" />
              {isGrowing && time >= duration - 1
                ? "Waiting for more footage"
                : "Loading your timelapse"}
            </div>
          )}
          {error && (
            <div className="player-error" role="alert">
              <p>{error}</p>
              <button
                className="primary-button"
                onClick={() => {
                  initialPosition.current = position.current;
                  desiredPlayback.current = true;
                  setRetry((value) => value + 1);
                }}
              >
                Retry playback
              </button>
            </div>
          )}
          {mobile && started && (
            <button
              className={`center-play ${centerVisible ? "" : "center-play-hidden"}`}
              aria-label={paused ? "Play" : "Pause"}
              aria-hidden={!centerVisible}
              tabIndex={centerVisible ? 0 : -1}
              onClick={toggle}
            >
              {paused ? (
                <Play fill="currentColor" />
              ) : (
                <Pause fill="currentColor" />
              )}
            </button>
          )}
        </div>
        <header className="watch-nav chrome">
          <Link to="/" className="brand" aria-label="Timelapse today">
            <Film />
            <span>TIMELAPSE</span>
          </Link>
          <div className="nav-links">
            <Link to="/archive" className="nav-button">
              <CalendarDays size={17} />
              <span>Archive</span>
            </Link>
            <button
              className="icon-button settings-button"
              aria-label="Player settings"
              onClick={() => {
                setSpeedOpen(false);
                setSettingsOpen(true);
              }}
            >
              <Settings size={20} />
            </button>
          </div>
        </header>
        {started && (
          <div className="player-bottom chrome">
            <Timeline
              time={time}
              duration={duration}
              buffered={buffered}
              step={seekStep}
              onSeek={seek}
              onScrubbing={setScrubbing}
            />
            <div className="control-bar">
              <div className="control-group">
                {!mobile && (
                  <PlayButton
                    className="icon-button"
                    aria-label={paused ? "Play" : "Pause"}
                    onClick={() => {
                      desiredPlayback.current = paused;
                      showControls();
                    }}
                  >
                    {paused ? (
                      <Play fill="currentColor" />
                    ) : (
                      <Pause fill="currentColor" />
                    )}
                  </PlayButton>
                )}
                <button
                  className="icon-button"
                  aria-label="Start over"
                  onClick={() => seek(0)}
                >
                  <SkipBack size={19} />
                </button>
                {mobile && zoom.zoomed && (
                  <button
                    className="icon-button"
                    aria-label="Reset zoom"
                    title="Reset zoom"
                    onClick={() => {
                      zoom.reset();
                      showControls();
                    }}
                  >
                    <ZoomOut size={20} />
                  </button>
                )}
                <button
                  className="latest-button"
                  aria-label="Jump to latest available footage"
                  onClick={() => seek(Math.max(0, duration - 0.1))}
                >
                  {isGrowing && <span className="pulse-dot" />} Latest
                </button>
              </div>
              <div className="control-group">
                <SpeedPopover
                  speed={speed}
                  onChange={setSpeed}
                  open={speedOpen}
                  onOpenChange={showSpeed}
                />
                {pip && (
                  <button
                    className="icon-button"
                    aria-label="Picture in picture"
                    onClick={() => {
                      if (document.pictureInPictureElement)
                        void document.exitPictureInPicture().catch(() => {});
                      else
                        void video.current
                          ?.requestPictureInPicture()
                          .catch(() =>
                            announce("Picture-in-picture is unavailable"),
                          );
                    }}
                  >
                    <PictureInPicture2 size={20} />
                  </button>
                )}
                <button
                  className="icon-button"
                  aria-label={full ? "Exit fullscreen" : "Enter fullscreen"}
                  onClick={() => void fullscreen()}
                >
                  {full ? <Minimize /> : <Maximize />}
                </button>
              </div>
            </div>
          </div>
        )}
        <Dialog.Root open={settingsOpen} onOpenChange={setSettingsOpen}>
          <Dialog.Portal container={shell.current}>
            <Dialog.Overlay className="dialog-overlay" />
            <Dialog.Content
              className="settings-dialog"
              onOpenAutoFocus={cancelBoost}
            >
              <div className="dialog-heading">
                <div>
                  <span className="eyebrow">MAKE IT YOUR PACE</span>
                  <Dialog.Title>Player settings</Dialog.Title>
                </div>
                <Dialog.Close
                  className="icon-button"
                  aria-label="Close settings"
                >
                  <X />
                </Dialog.Close>
              </div>
              <Dialog.Description className="muted">
                Fine-tune how you watch. The recording itself stays the same.
              </Dialog.Description>
              <div className="seek-setting">
                <label htmlFor="seek-step">Skip forward / back</label>
                <div>
                  <input
                    id="seek-step"
                    type="number"
                    min={1}
                    max={120}
                    step={1}
                    value={seekDraft}
                    onBlur={() => setSeekDraft(String(seekStep))}
                    onChange={(event) => {
                      setSeekDraft(event.target.value);
                      const value = Number(event.target.value);
                      if (
                        Number.isInteger(value) &&
                        value >= 1 &&
                        value <= 120
                      ) {
                        setSeekStep(value);
                        setStored("timelapse.seekSeconds", String(value));
                      }
                    }}
                  />
                  <span>seconds</span>
                </div>
              </div>
              <p className="setting-note">
                Skip distance is remembered for this browser. Each new day
                starts at 1× speed.
              </p>
              <div className="gesture-help">
                <CircleHelp size={17} />
                <p>
                  {mobile
                    ? "Tap the video to show playback controls. Pinch to zoom and drag to pan. "
                    : "Click the video to play or pause. "}
                  Double-tap either side to skip. Hold the video to speed up
                  when not zoomed in. Use ← / → on a keyboard.
                </p>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </Container>
    </VideoPlayer>
  );
}
