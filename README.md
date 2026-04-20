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

Find shortest holder path to a language root from an address:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli find-root-path \
  --db ./out/heap.db \
  --addr 0x5b97495fd0
```

Optional search limits:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli find-root-path \
  --db ./out/heap.db \
  --addr 0x5b97495fd0 \
  --max-depth 10 \
  --max-fanout 512
```

Inspect JS object properties (including array values like `knapi_refs_test`):

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli inspect-js-props \
  --db ./out/heap.db \
  --addr 1020039
```

Search Kotlin holders by a value (OQL-like field-value lookup):

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli search-kt-by-value \
  --db ./out/heap.db \
  --value 0x5d0b7b6a60
```

## TODO

- Replace type-name root heuristics with real GC root extraction from snapshots (both HPROF and ArkTS heapsnapshot).
- Populate and use a real `roots` table, then make `find-root-path` stop on true roots instead of heuristics.
- Add cross-language orchestration command: run JS/Kotlin root-path search step-by-step through `xrefs/cross_links` bridges.
- Improve cross-language orchestration command (`analyze-chain`) with richer bridge scoring/filtering and stronger false-positive controls.
- Support Top-K shortest root paths (not only one shortest path), with stable ordering and deduped output.
- Add configurable pseudo-root behavior (`addr in {0,1}`) instead of hardcoded default.
- Improve root-path labeling: include decoded field/property names for each hop with clearer source/target semantics.
- Add cycle detection in orchestration layer using visited root/bridge state across languages.
- Add optional dominator-tree based ranking for severity (after true-root pipeline is ready).
- Add schema constraints/index strategy for long-term quality (e.g., uniqueness guards for object identity per snapshot/lang).
- Add compact/analysis modes: temporary indexes during extraction, then drop and `VACUUM` for smaller output DB.
- Expand rule engine for vendor differences (multi-source fields, virtual-node patterns, mixed radix parsing).
- Add integration tests with synthetic cross-language leak fixtures and expected-path assertions.
- Add edge-type policy validation for strong-reference analysis (default exclude weak/shortcut, verify hidden/internal semantics per runtime).
