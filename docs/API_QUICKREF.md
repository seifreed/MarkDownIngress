# API Quick Reference

Quick reference guide for MarkDownIngress API Server v0.6.0

## Installation

```bash
# Install with API support
pip install -e '.[api]'

# Or install all features
pip install -e '.[all]'
```

## Start Server

```bash
# Python
python -m markdown_ingress.api_server

# Uvicorn
uvicorn markdown_ingress.api_server:app --host 0.0.0.0 --port 8000

# Docker
docker-compose up -d
```

## Endpoints

### POST /ingest
Basic URL ingestion
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "mode": "auto"}'
```

### POST /ingest/retry
Ingestion with retry logic
```bash
curl -X POST http://localhost:8000/ingest/retry \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "max_retries": 3}'
```

### POST /ingest/batch
Batch URL processing
```bash
curl -X POST http://localhost:8000/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com", "https://example.org"]}'
```

### POST /security/report
Security analysis report
```bash
curl -X POST http://localhost:8000/security/report \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "strict": true}'
```

### GET /health
Health check
```bash
curl http://localhost:8000/health
```

## Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| url | string | required | - | Target URL |
| mode | string | "auto" | auto/fast/render | Ingestion mode |
| strict | boolean | true | - | Strict security |
| timeout | integer | 60 | 1-300 | Timeout (seconds) |
| stealth | boolean | false | - | Stealth mode |
| max_retries | integer | 3 | 1-10 | Retry attempts |

## Response Fields

```json
{
  "markdown": "# Title\nContent...",
  "metadata": {
    "url": "https://example.com",
    "title": "Page Title",
    "mode": "fast",
    "token_savings": {...}
  },
  "token_estimate": 150,
  "injection_score": 0.0,
  "flags": [],
  "content_hash": "sha256:...",
  "removed_elements": {...}
}
```

## Docker Commands

```bash
# Build
docker build -t markdown-ingress:0.6.0 .

# Run
docker-compose up -d

# Logs
docker-compose logs -f api

# Stop
docker-compose down

# Health check
curl http://localhost:8000/health
```

## See Also

- Full documentation: [docs/API_SERVER.md](API_SERVER.md)
- Interactive docs: http://localhost:8000/docs
- OpenAPI spec: http://localhost:8000/openapi.json
