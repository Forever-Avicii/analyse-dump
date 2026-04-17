PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  path TEXT NOT NULL,
  captured_at TEXT,
  meta_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS objects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  lang TEXT NOT NULL,
  obj_addr TEXT NOT NULL,
  type_name TEXT,
  shallow_size INTEGER,
  retained_size INTEGER,
  extra_json TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  from_obj_addr TEXT NOT NULL,
  to_obj_addr TEXT NOT NULL,
  edge_type TEXT,
  field_name_or_index TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS roots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  obj_addr TEXT NOT NULL,
  root_type TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS xrefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  lang TEXT NOT NULL,
  ref_addr TEXT NOT NULL,
  owner_obj_addr TEXT NOT NULL,
  ref_kind TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS cross_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  js_obj_addr TEXT,
  kt_obj_addr TEXT,
  ref_addr TEXT NOT NULL,
  confidence REAL,
  evidence_json TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS heap_strings (
  snapshot_id INTEGER NOT NULL,
  string_index INTEGER NOT NULL,
  value TEXT,
  PRIMARY KEY(snapshot_id, string_index),
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_objects_snapshot_addr
ON objects(snapshot_id, obj_addr);

CREATE INDEX IF NOT EXISTS idx_edges_snapshot_from
ON edges(snapshot_id, from_obj_addr);

CREATE INDEX IF NOT EXISTS idx_edges_snapshot_to
ON edges(snapshot_id, to_obj_addr);

CREATE INDEX IF NOT EXISTS idx_xrefs_snapshot_ref
ON xrefs(snapshot_id, ref_addr);

CREATE INDEX IF NOT EXISTS idx_cross_links_snapshot_ref
ON cross_links(snapshot_id, ref_addr);

CREATE INDEX IF NOT EXISTS idx_heap_strings_snapshot_idx
ON heap_strings(snapshot_id, string_index);
