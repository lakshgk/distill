## What this PR does

Restructures documentation to serve two audiences: developers using distill-core
as a library, and teams running Distill as a self-hosted service. Adds four new
doc pages, updates the GitHub Pages landing page, and updates README with new
sections.

## Files changed

New docs:
- `docs/quickstart-library.md` — Python library usage guide with ParseOptions reference
- `docs/quickstart-service.md` — Docker Compose deployment guide
- `docs/api-reference.md` — complete REST API reference (all endpoints, parameters, responses)
- `docs/configuration.md` — all environment variables with defaults and descriptions

Updated:
- `docs/index.html` — format count 19, two-mode fork, new feature cards, expanded format table
- `docs/architecture.md` — deployment section added
- `docs/parsers.md` — EPUB, WSDL, JSON, SQL parser sections
- `README.md` — two-mode framing, async jobs, progress streaming, docs index, metrics section

## Tests

No code changes — documentation only. Full test suite still passes.

## How to verify

```bash
# All doc files exist
ls docs/quickstart-library.md docs/quickstart-service.md \
   docs/api-reference.md docs/configuration.md

# Format count updated
grep "19" docs/index.html | head -1

# Doc links in README
grep -c "quickstart-library\|quickstart-service\|api-reference\|configuration" README.md
```

## Merge order

Depends on: `feat/format-parsers` (docs reference all formats).
Second-to-last PR — merge before `feat/traffic-metrics`.
