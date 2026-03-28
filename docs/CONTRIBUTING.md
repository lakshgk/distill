# Contributing to Distill

## Getting started

```bash
git clone https://github.com/lakshgk/distill.git
cd distill

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e "packages/core[dev,google,vision,ocr]"
pip install -e packages/app
```

Run the full test suite to confirm everything is working:

```bash
pytest packages/core/tests -v
pytest packages/app/tests  -v
```

## Adding a parser

Implement `Parser` and register it:

```python
from distill.parsers.base import Parser, ParseOptions
from distill.ir import Document
from distill.registry import registry

@registry.register
class MyFormatParser(Parser):
    extensions = [".myext"]
    mime_types = ["application/x-myformat"]
    requires   = ["my-library"]          # pip packages needed

    def parse(
        self,
        source: str | Path | bytes,
        options: ParseOptions | None = None,
    ) -> Document:
        ...
```

- Return a `distill.ir.Document` — see `ir.py` for the full node hierarchy.
- Raise `distill.parsers.base.ParseError` for expected failures (bad file, missing dep).
- Add tests in `packages/core/tests/test_<format>.py` that cover the happy path and common edge cases.

## Project layout

```
distill/
├── packages/
│   ├── core/                    # distill-core: conversion library
│   │   ├── distill/
│   │   │   ├── ir.py            # Intermediate Representation nodes
│   │   │   ├── registry.py      # Parser registry + format detection
│   │   │   ├── renderer.py      # IR → Markdown
│   │   │   ├── quality.py       # Quality scoring
│   │   │   └── parsers/         # One file per format
│   │   └── tests/
│   └── app/                     # distill-app: web UI + REST API
│       ├── distill_app/
│       │   ├── server.py        # FastAPI app (GET /, POST /api/convert)
│       │   └── static/          # index.html served at GET /
│       └── tests/
└── docs/                        # Architecture, parser, and contributing docs
```

## Pull request checklist

- [ ] `pytest packages/core/tests -v` passes with no new failures
- [ ] `pytest packages/app/tests  -v` passes with no new failures
- [ ] New behaviour has tests
- [ ] `docs/architecture.md` updated if the pipeline, IR, or public API changed
- [ ] `docs/parsers.md` updated if a parser was added or changed

## Code style

- Python 3.10+, type-annotated function signatures
- No external formatters enforced — match the style of the file you are editing
- Keep `distill-core` free of UI and server dependencies

## License

By contributing you agree that your changes will be released under the [MIT License](../LICENSE).
