# distill-app

Local web UI for [distill-core](../core/README.md).

## Install

```bash
pip install distill-app
```

## Run

```bash
distill-app
```

Opens a browser at `http://localhost:7860`.

## Options

```
distill-app --host 0.0.0.0 --port 8080   # bind to all interfaces
distill-app --no-browser                   # don't open browser automatically
```

## API

The server also exposes a REST endpoint:

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@report.pdf" \
  -F "include_metadata=true" \
  -F "max_rows=500" \
  -F "enable_ocr=false"
```

Response:

```json
{
  "markdown": "# Report\n\n...",
  "quality": { "overall": 0.92, "headings": 1.0, "tables": 0.85, "lists": 1.0, "efficiency": 0.78 },
  "stats":   { "words": 1420, "pages": 5, "format": "PDF" },
  "warnings": []
}
```
