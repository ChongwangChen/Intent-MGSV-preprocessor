PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL UNIQUE,
    douyin_video_id TEXT,
    video_path TEXT,
    clip_audio_path TEXT,
    creator_name TEXT,
    video_title TEXT,
    hashtags TEXT,
    full_desc TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    total_frames INTEGER,
    frame_rate REAL,
    row_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    artist TEXT,
    album TEXT,
    full_song_path TEXT,
    qq_song_mid TEXT,
    qq_song_id TEXT,
    source TEXT,
    fingerprint TEXT,
    row_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(qq_song_mid),
    UNIQUE(full_song_path)
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
    annotator_id TEXT NOT NULL,
    music_id TEXT,
    sync_level TEXT,
    music_start REAL,
    music_end REAL,
    shot_points TEXT,
    shot_points_3 TEXT,
    shot_points_5 TEXT,
    emotion TEXT,
    style TEXT,
    usage_scene TEXT,
    seg_scores_3 TEXT,
    seg_scores_5 TEXT,
    vocal_presence TEXT,
    genre TEXT,
    song_verified TEXT,
    recog_confidence TEXT,
    recog_note TEXT,
    match_score REAL,
    status TEXT NOT NULL DEFAULT 'in_progress',
    row_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, annotator_id)
);

CREATE TABLE IF NOT EXISTS annotation_assignments (
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    annotator_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress',
    lease_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(video_id, annotator_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT,
    event_type TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS music_preparations (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
    recognized_title TEXT,
    recognized_artist TEXT,
    recognition_confidence REAL,
    recognition_votes INTEGER,
    genre_suggestion TEXT,
    genre_source TEXT,
    genre_confidence REAL,
    qq_song_mid TEXT,
    qq_match_score REAL,
    download_source TEXT,
    full_song_path TEXT,
    song_offset REAL,
    video_audio_start REAL NOT NULL DEFAULT 0,
    aligned_duration REAL,
    match_score REAL,
    status TEXT NOT NULL DEFAULT 'recognized',
    error TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS song_reviews (
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    reviewer_id TEXT NOT NULL,
    song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
    song_correct INTEGER,
    alignment_correct INTEGER,
    corrected_offset REAL,
    final_genre TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(video_id, reviewer_id)
);

CREATE INDEX IF NOT EXISTS idx_annotations_annotator_status
ON annotations(annotator_id, status);

CREATE INDEX IF NOT EXISTS idx_assignments_status_lease
ON annotation_assignments(status, lease_until);

CREATE INDEX IF NOT EXISTS idx_jobs_status
ON jobs(status, job_type);

CREATE INDEX IF NOT EXISTS idx_music_preparations_status
ON music_preparations(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_song_reviews_reviewer_status
ON song_reviews(reviewer_id, status);
