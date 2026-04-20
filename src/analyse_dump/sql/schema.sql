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
  lang INTEGER NOT NULL,                 -- js=0, kotlin=1
  obj_addr INTEGER NOT NULL,
  type_name TEXT,
  shallow_size INTEGER,
  heap_index INTEGER,
  name_index INTEGER,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  from_obj_addr INTEGER NOT NULL,
  to_obj_addr INTEGER NOT NULL,
  edge_type INTEGER NOT NULL,            -- enum, see const.py
  name_kind INTEGER NOT NULL DEFAULT 0,  -- none=0, string_index=1, array_index=2, field_name=3
  name_num INTEGER,
  name_text TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS object_fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  lang INTEGER NOT NULL,
  obj_addr INTEGER NOT NULL,
  field_name TEXT NOT NULL,
  field_value_int INTEGER,
  field_value_text TEXT,
  value_type TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS xrefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  lang INTEGER NOT NULL,
  ref_addr INTEGER NOT NULL,
  owner_obj_addr INTEGER NOT NULL,
  ref_kind INTEGER NOT NULL,             -- stable_ref=1, napi_ref=2
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS cross_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  js_obj_addr INTEGER,
  kt_obj_addr INTEGER,
  ref_addr INTEGER NOT NULL,
  ref_kind INTEGER NOT NULL,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS root_distance (
  snapshot_id INTEGER NOT NULL,
  lang INTEGER NOT NULL,
  obj_addr INTEGER NOT NULL,
  dist INTEGER NOT NULL,
  parent_addr INTEGER,
  PRIMARY KEY(snapshot_id, lang, obj_addr),
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS root_distance_cache (
  snapshot_id INTEGER NOT NULL,
  lang INTEGER NOT NULL,
  profile TEXT NOT NULL,
  obj_addr INTEGER NOT NULL,
  dist INTEGER NOT NULL,
  parent_addr INTEGER,
  PRIMARY KEY(snapshot_id, lang, profile, obj_addr),
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

CREATE INDEX IF NOT EXISTS idx_objects_snapshot_type
ON objects(snapshot_id, type_name);

CREATE INDEX IF NOT EXISTS idx_edges_snapshot_from
ON edges(snapshot_id, from_obj_addr);

CREATE INDEX IF NOT EXISTS idx_edges_snapshot_name_lookup
ON edges(snapshot_id, name_kind, name_num);

CREATE INDEX IF NOT EXISTS idx_object_fields_snapshot_field
ON object_fields(snapshot_id, field_name);

CREATE INDEX IF NOT EXISTS idx_object_fields_kt_ref_int
ON object_fields(snapshot_id, lang, field_name, field_value_int);

CREATE INDEX IF NOT EXISTS idx_xrefs_snapshot_ref
ON xrefs(snapshot_id, ref_addr);

CREATE INDEX IF NOT EXISTS idx_cross_links_snapshot_ref
ON cross_links(snapshot_id, ref_addr);

CREATE INDEX IF NOT EXISTS idx_root_distance_snapshot_lang_dist
ON root_distance(snapshot_id, lang, dist);

CREATE INDEX IF NOT EXISTS idx_root_distance_cache_lookup
ON root_distance_cache(snapshot_id, lang, profile, dist);

CREATE INDEX IF NOT EXISTS idx_heap_strings_snapshot_value
ON heap_strings(snapshot_id, value);
