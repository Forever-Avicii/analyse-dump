# analyse-dump

Offline import tool for large heap dumps:

- `HPROF` (Kotlin/JVM side)
- `heapsnapshot` (ArkTS/V8 side)

The tool converts snapshots into a normalized SQLite database for later querying.

## Install

```bash
python3 -m pip install -e .
```

If your Python environment cannot install globally, run directly from source:

```bash
python3 -m pip install --target ./.deps ijson
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli --help
```

## Usage

Rebuild from scratch:

```bash
rm -f ./out/heap.db ./out/heap.db-shm ./out/heap.db-wal
```

Initialize DB schema:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli init-db --db ./out/heap.db
```

Import HPROF:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli import-hprof --db ./out/heap.db --input ./data/1.hprof
```

Import heapsnapshot:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli import-heapsnapshot --db ./out/heap.db --input ./data/1.heapsnapshot
```

Skip string table import (faster, less disk):

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli import-heapsnapshot --db ./out/heap.db --input ./data/1.heapsnapshot --skip-strings
```

## Notes

- `HPROF` parsing uses vendored `py-hprof` (`third_party/py-hprof`, MIT License).
- `py-hprof` loads the whole heap into memory. Prefer running on a machine with enough RAM.
- `heapsnapshot` parsing is streaming (`ijson`) and suitable for large files.

## DB tables

Core tables:

- `snapshots`
- `objects`
- `edges`
- `object_fields`
- `roots`
- `xrefs`
- `cross_links`
- `heap_strings`

This step only builds offline import capability. Cross-language link analysis can be added on top.

## Storage Notes

- Address fields are stored as `INTEGER` (not hex text).
- Enum fields are numeric:
  - `lang`: `js=0`, `kotlin=1`
  - `ref_kind`: `stable_ref=1`, `napi_ref=2`
  - `edge_type`/`name_kind`: see `src/analyse_dump/const.py`

## Configurable xrefs/cross-links

Build cross-language references from rules:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli link-xrefs \
  --db ./out/heap.db \
  --config ./config/xrefs.rules.example.json
```

Use explicit snapshot ids if needed:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli link-xrefs \
  --db ./out/heap.db \
  --config ./config/xrefs.rules.example.json \
  --js-snapshot-id 2 \
  --kt-snapshot-id 1
```
