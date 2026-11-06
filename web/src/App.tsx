import { useEffect, useState } from "react";
import {
  Link,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { DayPicker } from "react-day-picker";
import {
  ArrowLeft,
  ArrowUpRight,
  CalendarDays,
  Camera,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { emptyState, formatTime, prettyDate, todayIn, validDate } from "./lib";
import { useLibrary } from "./useLibrary";
import type { Library } from "./types";
import { Player } from "./components/Player";

function Watch({ library, now }: { library: Library; now: Date }) {
  const { date: parameter } = useParams();
  const location = useLocation();
  const today = todayIn(library.timezone, now);
  const [pin, setPin] = useState<{ date: string; navigation: string } | null>(
    null,
  );
  const pinnedDate = pin?.navigation === location.key ? pin.date : null;
  const date = parameter ?? pinnedDate ?? today;
  if (!validDate(date))
    return (
      <div className="message-page">
        <h1>That date doesn't exist.</h1>
        <Link className="primary-button" to="/archive">
          Open the archive
        </Link>
      </div>
    );
  const day = library.days.find((item) => item.date === date);
  return (
    <Player
      key={`${location.key}-${date}-${day?.generation_id ?? "pending"}`}
      library={library}
      day={day}
      date={date}
      now={now}
      onStart={() => setPin({ date, navigation: location.key })}
    />
  );
}

function Archive({ library, now }: { library: Library; now: Date }) {
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const today = todayIn(library.timezone, now);
  const days = library.days.filter((day) => day.ready);
  const latest = days[0]?.date ?? today;
  const earliest = days.at(-1)?.date ?? today;
  const requested = search.get("month") ?? latest.slice(0, 7);
  const monthKey =
    /^\d{4}-\d{2}$/.test(requested) && validDate(`${requested}-01`)
      ? requested
      : latest.slice(0, 7);
  const month = new Date(`${monthKey}-01T12:00:00`);
  const visible = days.filter((day) => day.date.startsWith(monthKey));
  const available = new Set(days.map((day) => day.date));
  const setMonth = (value: Date) =>
    setSearch({
      month: `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`,
    });
  const moveMonth = (direction: number) =>
    setMonth(
      new Date(month.getFullYear(), month.getMonth() + direction, 1, 12),
    );
  const label = new Intl.DateTimeFormat("en-GB", {
    month: "long",
    year: "numeric",
  }).format(month);
  return (
    <div className="archive-page">
      <header className="archive-nav">
        <Link className="brand" to="/">
          <img src="/timelapse-icon.svg" alt="" width={28} height={28} />
          <span>TIMELAPSE</span>
        </Link>
        <Link className="nav-button" to="/">
          <ArrowLeft size={16} /> Today's view
        </Link>
      </header>
      <main className="archive-main">
        <div className="archive-title">
          <div>
            <span className="eyebrow">
              {library.camera.replaceAll("_", " ")} / THE COLLECTION
            </span>
            <h1>
              A day worth
              <br />
              <span>looking back on.</span>
            </h1>
            <p>Small moments. A different perspective.</p>
          </div>
          <div className="archive-count">
            <strong>{days.length.toString().padStart(2, "0")}</strong>
            <span>days preserved</span>
          </div>
        </div>
        <div className="archive-body">
          <aside className="calendar-panel" aria-label="Choose an archive date">
            <div className="calendar-panel-heading">
              <CalendarDays size={18} />
              <span>Find a day</span>
            </div>
            <DayPicker
              mode="single"
              month={month}
              onMonthChange={setMonth}
              captionLayout="dropdown"
              startMonth={new Date(`${earliest.slice(0, 7)}-01T12:00:00`)}
              endMonth={new Date(`${today.slice(0, 7)}-01T12:00:00`)}
              weekStartsOn={1}
              showOutsideDays
              modifiers={{
                available: (date) =>
                  available.has(
                    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`,
                  ),
              }}
              disabled={(date) =>
                !available.has(
                  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`,
                )
              }
              onSelect={(date) => {
                if (date)
                  navigate(
                    `/days/${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`,
                  );
              }}
            />
            <div className="calendar-legend">
              <span className="small-dot" /> Timelapse available
            </div>
            <Link className="calendar-today" to="/">
              Return to today <ArrowUpRight size={17} />
            </Link>
          </aside>
          <section className="archive-videos" aria-label="Timelapses">
            <div className="month-heading">
              <div>
                <h2>{label}</h2>
                <span>
                  {visible.length}{" "}
                  {visible.length === 1 ? "timelapse" : "timelapses"}
                </span>
              </div>
              <div className="control-group">
                <button
                  className="icon-button"
                  aria-label="Previous month"
                  disabled={monthKey <= earliest.slice(0, 7)}
                  onClick={() => moveMonth(-1)}
                >
                  <ChevronLeft />
                </button>
                <button
                  className="icon-button"
                  aria-label="Next month"
                  disabled={monthKey >= today.slice(0, 7)}
                  onClick={() => moveMonth(1)}
                >
                  <ChevronRight />
                </button>
              </div>
            </div>
            {visible.length ? (
              <div className="day-grid">
                {visible.map((day) => (
                  <Link
                    className="day-card"
                    key={day.date}
                    to={`/days/${day.date}`}
                  >
                    <div className="card-poster">
                      {day.poster_url ? (
                        <img
                          src={day.poster_url}
                          alt=""
                          loading="lazy"
                          onError={(event) => {
                            event.currentTarget.style.display = "none";
                          }}
                        />
                      ) : (
                        <Camera size={32} strokeWidth={1} />
                      )}
                      <span className="card-duration">
                        {formatTime(day.duration)}
                      </span>
                      <span className="card-open">
                        <ArrowUpRight size={23} />
                      </span>
                    </div>
                    <div className="card-detail">
                      <div>
                        <span>{prettyDate(day.date)}</span>
                        <small>
                          {day.date === today
                            ? "Today, so far"
                            : day.complete
                              ? "A complete day"
                              : "A partial day"}
                        </small>
                      </div>
                      <span
                        className={`day-status ${day.complete ? "complete" : ""}`}
                      >
                        {day.date === today && !day.complete
                          ? "GROWING"
                          : day.complete
                            ? "COMPLETE"
                            : "PARTIAL"}
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="archive-empty">
                <Camera size={36} strokeWidth={1} />
                <h3>No timelapses this month</h3>
                <p>Choose a marked date or try another month.</p>
                {days.length === 0 && (
                  <p>{emptyState(library, today, undefined, now)}</p>
                )}
              </div>
            )}
          </section>
        </div>
        <footer className="archive-footer">
          <span>THE DAYS ARE LONG. THE MOMENTS ARE SMALL.</span>
          <span>{library.timezone}</span>
        </footer>
      </main>
    </div>
  );
}

export default function App() {
  const { library, error, now, refresh } = useLibrary();
  useEffect(() => {
    document.title = library
      ? `${library.camera.replaceAll("_", " ")} · Timelapse`
      : "Timelapse";
  }, [library]);
  if (!library)
    return (
      <div className="message-page">
        <img src="/timelapse-icon.svg" alt="" width={44} height={44} />
        <span className="eyebrow">TIMELAPSE</span>
        <h1>{error ? "A moment out of reach." : "Finding your days…"}</h1>
        <p role={error ? "alert" : "status"}>
          {error || "Connecting to the library."}
        </p>
        {error && (
          <button className="primary-button" onClick={refresh}>
            <RefreshCw size={16} /> Try again
          </button>
        )}
      </div>
    );
  return (
    <>
      {error && (
        <div className="connection-banner" role="status">
          {error}
          <button onClick={refresh}>Retry</button>
        </div>
      )}
      <Routes>
        <Route path="/" element={<Watch library={library} now={now} />} />
        <Route
          path="/days/:date"
          element={<Watch library={library} now={now} />}
        />
        <Route
          path="/archive"
          element={<Archive library={library} now={now} />}
        />
        <Route
          path="*"
          element={
            <div className="message-page">
              <h1>Nothing to see here.</h1>
              <Link className="primary-button" to="/">
                Back to today
              </Link>
            </div>
          }
        />
      </Routes>
    </>
  );
}
