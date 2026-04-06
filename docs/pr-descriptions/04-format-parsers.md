## What this PR does

Adds four new format parsers: EPUB (.epub), WSDL (.wsdl/.wsd), JSON Schema and
API dumps (.json), and SQL DDL (.sql). Each parser follows the standard
pipeline: parse to IR, render to Markdown.

## Files changed

Parsers:
- `packages/core/distill/parsers/epub.py` — EPUB via ZIP + HTMLParser delegation
- `packages/core/distill/parsers/wsdl.py` — WSDL 1.1/2.0 via defusedxml
- `packages/core/distill/parsers/json_parser.py` — JSON Schema, array dumps, flat objects
- `packages/core/distill/parsers/sql.py` — DDL structured rendering via sqlparse

Registration:
- `packages/core/distill/__init__.py` — imports for auto-registration
- `packages/app/distill_app/server.py` — new extensions in SUPPORTED set
- `packages/core/pyproject.toml` — [epub] and [sql] optional extras

Tests:
- `packages/core/tests/test_epub.py` — 13 tests
- `packages/core/tests/test_wsdl.py` — 13 tests
- `packages/core/tests/test_json_parser.py` — 25 tests
- `packages/core/tests/test_sql_parser.py` — 17 tests

Fixtures:
- `packages/core/tests/fixtures/simple.epub`
- `packages/core/tests/fixtures/simple.wsdl`
- `packages/core/tests/fixtures/simple_schema.json`
- `packages/core/tests/fixtures/simple_api_dump.json`
- `packages/core/tests/fixtures/simple.sql`

## Tests

All 68 new tests pass. Full suite green.

## How to verify

```bash
python -c "
from distill.registry import registry
for ext in ['test.epub', 'test.wsdl', 'test.json', 'test.sql']:
    print(ext, '->', registry.find(ext).__name__)
"
```

## Merge order

Depends on: `feat/queue-sse` (shares __init__.py, server.py, pyproject.toml).
