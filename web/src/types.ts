export interface Day {
  date: string;
  generation_id: string;
  poster_url: string | null;
  playlist_url: string;
  live_playlist_url: string;
  source_times_url?: string | null;
  duration: number;
  ready: boolean;
  recordings_available?: boolean;
  status: string;
  mode: string | null;
  complete: boolean;
  stream_ended: boolean;
  published_at: string | null;
  schedule_start: string | null;
  schedule_end: string | null;
  render_speed: number | null;
}

export interface Library {
  version: 1;
  camera: string;
  frigate_url?: string | null;
  frigate_retention_days?: number | null;
  timezone: string;
  schedule: { start: string; end: string };
  thumbnail_offset_seconds: number;
  published_at: string;
  days: Day[];
}
