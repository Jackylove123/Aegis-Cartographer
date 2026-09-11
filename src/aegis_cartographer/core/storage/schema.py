"""SQLite schema for Aegis map artifacts."""

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS map_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screens (
    screen_id TEXT PRIMARY KEY,
    semantic_name TEXT NOT NULL,
    screen_type TEXT NOT NULL DEFAULT 'page',
    business_domain TEXT,
    description TEXT NOT NULL DEFAULT '',
    coverage_status TEXT NOT NULL DEFAULT 'DISCOVERED',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screen_states (
    state_id TEXT PRIMARY KEY,
    screen_id TEXT NOT NULL REFERENCES screens(screen_id) ON DELETE CASCADE,
    state_name TEXT NOT NULL,
    state_type TEXT NOT NULL,
    package_name TEXT NOT NULL DEFAULT '',
    structural_hash TEXT NOT NULL,
    interactive_hash TEXT NOT NULL,
    landmark_hash TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    landmarks_json TEXT NOT NULL DEFAULT '[]',
    structural_features_json TEXT NOT NULL DEFAULT '[]',
    interactive_features_json TEXT NOT NULL DEFAULT '[]',
    signature_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS elements (
    element_id TEXT PRIMARY KEY,
    element_key TEXT NOT NULL,
    screen_id TEXT NOT NULL REFERENCES screens(screen_id) ON DELETE CASCADE,
    state_id TEXT NOT NULL REFERENCES screen_states(state_id) ON DELETE CASCADE,
    canonical_element_id TEXT,
    semantic_name TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    role TEXT NOT NULL DEFAULT 'UNKNOWN',
    is_actionable INTEGER NOT NULL DEFAULT 1,
    is_dynamic INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'unknown',
    actions_json TEXT NOT NULL DEFAULT '[]',
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    text TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    accessibility_id TEXT NOT NULL DEFAULT '',
    content_desc TEXT NOT NULL DEFAULT '',
    class_name TEXT NOT NULL DEFAULT '',
    bounds_json TEXT NOT NULL DEFAULT '[0,0,0,0]',
    occurrence_index INTEGER NOT NULL DEFAULT 1,
    selector TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    first_seen_at TEXT NOT NULL,
    last_verified_at TEXT,
    UNIQUE(state_id, element_key)
);

CREATE TABLE IF NOT EXISTS locators (
    locator_id TEXT PRIMARY KEY,
    element_id TEXT NOT NULL REFERENCES elements(element_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    strategy TEXT NOT NULL,
    value TEXT NOT NULL,
    priority INTEGER NOT NULL,
    scope TEXT NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_verified_at TEXT,
    UNIQUE(element_id, platform, strategy, value, scope)
);

CREATE TABLE IF NOT EXISTS transitions (
    transition_id TEXT PRIMARY KEY,
    from_state_id TEXT NOT NULL REFERENCES screen_states(state_id) ON DELETE CASCADE,
    element_id TEXT REFERENCES elements(element_id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    result_type TEXT NOT NULL,
    to_state_id TEXT REFERENCES screen_states(state_id) ON DELETE SET NULL,
    maestro_commands_json TEXT NOT NULL DEFAULT '[]',
    restore_strategy_json TEXT NOT NULL DEFAULT '[]',
    observed_count INTEGER NOT NULL DEFAULT 1,
    success_count INTEGER NOT NULL DEFAULT 1,
    last_verified_at TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    state_id TEXT NOT NULL REFERENCES screen_states(state_id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    structural_hash TEXT NOT NULL,
    interactive_hash TEXT NOT NULL,
    landmark_hash TEXT NOT NULL,
    state_type TEXT NOT NULL,
    package_name TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT,
    account_state TEXT NOT NULL DEFAULT '',
    network_state TEXT NOT NULL DEFAULT '',
    trigger_transition_id TEXT REFERENCES transitions(transition_id) ON DELETE SET NULL,
    signature_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paths (
    path_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    from_type TEXT NOT NULL,
    from_id TEXT NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '[]',
    maestro_flow_path TEXT,
    duration_ms INTEGER,
    success_count INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    UNIQUE(target_type, target_id, from_type, from_id)
);


CREATE TABLE IF NOT EXISTS embeddings (
    embedding_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK(target_type IN ('element', 'screen')),
    target_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector_blob BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(target_type, target_id, model_name, model_version)
);

CREATE VIRTUAL TABLE IF NOT EXISTS element_search USING fts5(
    element_id UNINDEXED,
    searchable_text
);

CREATE VIRTUAL TABLE IF NOT EXISTS screen_search USING fts5(
    screen_id UNINDEXED,
    searchable_text
);

CREATE INDEX IF NOT EXISTS idx_screen_states_screen
    ON screen_states(screen_id);
CREATE INDEX IF NOT EXISTS idx_elements_state
    ON elements(state_id);
CREATE INDEX IF NOT EXISTS idx_elements_screen
    ON elements(screen_id);
CREATE INDEX IF NOT EXISTS idx_locators_element
    ON locators(element_id);
CREATE INDEX IF NOT EXISTS idx_transitions_from
    ON transitions(from_state_id);
CREATE INDEX IF NOT EXISTS idx_transitions_to
    ON transitions(to_state_id);
CREATE INDEX IF NOT EXISTS idx_observations_state
    ON observations(state_id);
"""
