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
- `roots`
- `xrefs`
- `cross_links`
- `heap_strings`

This step only builds offline import capability. Cross-language link analysis can be added on top.
