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
- `root_distance`
- `root_distance_cache`
- `heap_strings`

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

Materialize `roots` table (`native|heuristic|mixed`, default `native`):

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli build-roots \
  --db ./out/heap.db \
  --roots-mode native
```

JS root rule used in `native` mode:

- `is_user_root(node)`: `node.type_name != 'synthetic'` OR `node.name == '(Document DOM trees)'`
- `distance_from_runtime_anchor == 1`: approximated as an incoming edge from anchor node `0` or `1`

This means JS roots are treated as user-facing GC roots, not the raw anchor nodes themselves.

Build distance-to-root cache:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli build-root-distance \
  --db ./out/heap.db \
  --lang js \
  --roots-mode native \
  --js-root-types __never_match__

PYTHONPATH=src:./.deps python3 -m analyse_dump.cli build-root-distance \
  --db ./out/heap.db \
  --lang kotlin \
  --roots-mode native
```

Analyze cross-language chains:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli analyze-chain \
  --db ./out/heap.db \
  --addr 1954511 \
  --lang js \
  --roots-mode native \
  --max-steps 3 \
  --max-depth 12 \
  --max-fanout 512
```

Structured output + narrative:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli analyze-chain \
  --db ./out/heap.db \
  --addr 1954511 \
  --lang js \
  --json \
  --narrative
```

Agent-style diagnosis:

```bash
PYTHONPATH=src:./.deps python3 -m analyse_dump.cli analyze-chain-agent \
  --db ./out/heap.db \
  --addr 1954511 \
  --lang js \
  --roots-mode native
```

## TODO

- Replace heuristic root extraction with true runtime GC root parsing from snapshots (especially ArkTS side).
- Add root-kind taxonomy and confidence levels in `roots`.
- Improve branch ranking in `analyze-chain` (prefer business objects, deprioritize framework/global paths).
- Add Top-K chain outputs instead of returning only the first terminal/loop branch.
- Improve root-path and chain JSON schema (stable node/edge ids instead of stringified dataclass values).
- Add stronger dedup/normalization for repeated loops and repeated bridge hops.
- Improve root-path labeling and explanations with richer field/property context.
- Add optional dominator-tree based ranking for severity (after true-root pipeline is ready).
- Add schema constraints/index strategy for long-term quality (uniqueness guards for object identity per snapshot/lang).
- Add compact/analysis modes: temporary indexes during extraction, then drop and `VACUUM` for smaller output DB.
- Expand rule engine for vendor differences (multi-source fields, virtual-node patterns, mixed radix parsing).
- Add integration tests with synthetic cross-language leak fixtures and expected-path assertions.
- Add edge-type policy validation for strong-reference analysis (default exclude weak/shortcut, verify hidden/internal semantics per runtime).
