# MarkDownIngress API Server

FastAPI-based REST API server for MarkDownIngress web content ingestion.

## Features

- **RESTful API** - Clean, documented endpoints for all operations
- **Interactive Docs** - Automatic Swagger/OpenAPI documentation
- **Docker Support** - Production-ready containerization
- **Batch Processing** - Ingest multiple URLs in a single request
- **Security Reports** - Generate detailed security analysis
- **Retry Logic** - Built-in retry with escalation strategies

## Quick Start

### Local Development

#### 1. Install Dependencies

```bash
# Install with API dependencies
pip install fastapi uvicorn[standard] pydantic

# Install MarkDownIngress with render support
pip install -e '.[render]'
playwright install chromium
```

#### 2. Run the Server

```bash
# Using Python
python -m markdown_ingress.api_server

# Or using uvicorn directly
uvicorn markdown_ingress.api_server:app --host 0.0.0.0 --port 8000 --reload
```

#### 3. Access the API

- **API Root**: http://localhost:8000
- **Interactive Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health

## Docker Deployment

### Build and Run with Docker Compose

```bash
# Build and start the service
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop the service
docker-compose down
```

### Build Docker Image Manually

```bash
# Build the image
docker build -t markdown-ingress:0.6.0 .

# Run the container
docker run -d \
  --name markdown-ingress-api \
  -p 8000:8000 \
  -v markdown-ingress-cache:/app/cache \
  markdown-ingress:0.6.0

# Check health
curl http://localhost:8000/health
```

### Docker Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PYTHONUNBUFFERED` | `1` | Enable unbuffered Python output |
| `LOG_LEVEL` | `info` | Logging level (debug, info, warning, error) |
| `PLAYWRIGHT_BROWSERS_PATH` | `/root/.cache/ms-playwright` | Playwright browser cache location |

## API Endpoints

### 1. Single URL Ingestion

**POST** `/ingest`

Ingest a single URL and return structured markdown.

**Request Body:**
```json
{
  "url": "https://example.com",
  "mode": "auto",
  "strict": true,
  "timeout": 60,
  "model": "gpt-4",
  "stealth": false,
  "disable_http2": false,
  "extreme_mode": false
}
```

**Response:**
```json
{
  "markdown": "# Example Domain\n\nThis domain is...",
  "metadata": {
    "url": "https://example.com",
    "title": "Example Domain",
    "mode": "fast",
    "token_savings": {...}
  },
  "token_estimate": 150,
  "injection_score": 0.0,
  "flags": [],
  "content_hash": "sha256:abc123...",
  "removed_elements": {...}
}
```

**cURL Example:**
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "mode": "auto",
    "timeout": 60
  }'
```

### 2. Retry Ingestion

**POST** `/ingest/retry`

Ingest with automatic retry logic and timeout escalation.

**Request Body:**
```json
{
  "url": "https://example.com",
  "mode": "auto",
  "strict": true,
  "model": "gpt-4",
  "max_retries": 3,
  "enable_stealth": true,
  "initial_timeout": 60.0
}
```

**cURL Example:**
```bash
curl -X POST http://localhost:8000/ingest/retry \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://difficult-site.com",
    "max_retries": 5,
    "enable_stealth": true
  }'
```

### 3. Batch Ingestion

**POST** `/ingest/batch`

Ingest multiple URLs in a single request (max 100 URLs).

**Request Body:**
```json
{
  "urls": [
    "https://example.com",
    "https://example.org",
    "https://example.net"
  ],
  "mode": "auto",
  "strict": true,
  "timeout": 60,
  "model": "gpt-4"
}
```

**Response:**
```json
{
  "results": [
    {
      "url": "https://example.com",
      "success": true,
      "data": {...}
    },
    {
      "url": "https://example.org",
      "success": false,
      "error": "Timeout error"
    }
  ],
  "success_count": 2,
  "failure_count": 1
}
```

**cURL Example:**
```bash
curl -X POST http://localhost:8000/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://example.com",
      "https://example.org"
    ],
    "mode": "fast"
  }'
```

### 4. Security Report

**POST** `/security/report`

Generate comprehensive security analysis for a URL.

**Request Body:**
```json
{
  "url": "https://example.com",
  "mode": "auto",
  "strict": true,
  "timeout": 60,
  "model": "gpt-4"
}
```

**Response:**
```json
{
  "injection_score": 0.0,
  "risk_level": "LOW",
  "flags": [],
  "hidden_content_detected": false,
  "hidden_elements_count": 0,
  "url": "https://example.com",
  "title": "Example Domain",
  "token_estimate": 150,
  "token_reduction_percent": 95.2,
  "content_hash": "sha256:abc123...",
  "structural_hash": "sha256:def456...",
  "removed_elements": {...}
}
```

**cURL Example:**
```bash
curl -X POST http://localhost:8000/security/report \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://suspicious-site.com",
    "strict": true
  }'
```

### 5. Health Check

**GET** `/health`

Check API server health status.

**Response:**
```json
{
  "status": "healthy",
  "version": "0.6.0",
  "service": "MarkDownIngress API"
}
```

**cURL Example:**
```bash
curl http://localhost:8000/health
```

## Parameters Reference

### Common Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Target URL to ingest |
| `mode` | string | `"auto"` | Ingestion mode: `auto`, `fast`, or `render` |
| `strict` | boolean | `true` | Enable strict security mode |
| `timeout` | integer | `60` | Request timeout (1-300 seconds) |
| `model` | string | `"gpt-4"` | LLM model for token estimation |

### Render Mode Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stealth` | boolean | `false` | Enable stealth mode to bypass bot detection |
| `disable_http2` | boolean | `false` | Disable HTTP/2 protocol |
| `extreme_mode` | boolean | `false` | Enable extreme timeouts (up to 300s) |

### Retry Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_retries` | integer | `3` | Maximum retry attempts (1-10) |
| `enable_stealth` | boolean | `true` | Enable stealth on retry attempts |
| `initial_timeout` | float | `60.0` | Initial timeout (escalates on retries) |

## Response Fields

### IngestResponse

| Field | Type | Description |
|-------|------|-------------|
| `markdown` | string | Cleaned markdown content |
| `metadata` | object | URL, title, timing, and processing info |
| `token_estimate` | integer | Estimated token count |
| `injection_score` | float | Security risk score (0.0-1.0) |
| `flags` | array | Security warning flags |
| `content_hash` | string | SHA256 hash of content |
| `removed_elements` | object | Count of removed/sanitized elements |

## Production Deployment

### Docker Compose Production Setup

```yaml
version: '3.8'

services:
  api:
    image: markdown-ingress:0.6.0
    container_name: markdown-ingress-api
    ports:
      - "8000:8000"
    volumes:
      - markdown-ingress-cache:/app/cache
    environment:
      - PYTHONUNBUFFERED=1
      - LOG_LEVEL=info
    restart: always
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 1G

  # Optional: Nginx reverse proxy
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - api
    restart: always

volumes:
  markdown-ingress-cache:
```

### Health Checks

The API includes built-in health checks:

```bash
# Docker health check (automatic)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:8000/health

# Manual health check
curl http://localhost:8000/health
```

### Resource Requirements

**Minimum:**
- CPU: 0.5 cores
- Memory: 512 MB
- Disk: 2 GB (for Playwright browsers)

**Recommended:**
- CPU: 2 cores
- Memory: 2 GB
- Disk: 5 GB

**High-Load:**
- CPU: 4+ cores
- Memory: 4+ GB
- Disk: 10+ GB

## Error Handling

All endpoints return standard HTTP status codes:

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request (invalid parameters or Playwright not installed) |
| 500 | Internal Server Error (ingestion failed) |

**Error Response Format:**
```json
{
  "detail": "Error message describing what went wrong"
}
```

## Integration Examples

### Python Client

```python
import requests

# Single URL ingestion
response = requests.post(
    "http://localhost:8000/ingest",
    json={
        "url": "https://example.com",
        "mode": "auto",
        "timeout": 60
    }
)
doc = response.json()
print(doc["markdown"])

# Batch ingestion
response = requests.post(
    "http://localhost:8000/ingest/batch",
    json={
        "urls": [
            "https://example.com",
            "https://example.org"
        ],
        "mode": "fast"
    }
)
results = response.json()
print(f"Success: {results['success_count']}, Failed: {results['failure_count']}")
```

### JavaScript/Node.js Client

```javascript
const axios = require('axios');

// Single URL ingestion
async function ingest(url) {
  const response = await axios.post('http://localhost:8000/ingest', {
    url: url,
    mode: 'auto',
    timeout: 60
  });
  return response.data;
}

// Usage
ingest('https://example.com')
  .then(doc => console.log(doc.markdown))
  .catch(err => console.error(err));
```

### cURL Examples

```bash
# Basic ingestion
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# With stealth mode
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://protected-site.com",
    "mode": "render",
    "stealth": true
  }'

# Batch processing
curl -X POST http://localhost:8000/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://site1.com", "https://site2.com"],
    "mode": "fast"
  }' | jq .
```

## Troubleshooting

### Common Issues

**1. "Render mode requires Playwright"**
```bash
# Install Playwright
pip install playwright
playwright install chromium
```

**2. Container health check failing**
```bash
# Check container logs
docker-compose logs api

# Check if service is responding
curl http://localhost:8000/health
```

**3. Timeout errors**
```bash
# Increase timeout or use retry endpoint
curl -X POST http://localhost:8000/ingest/retry \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://slow-site.com",
    "max_retries": 5,
    "initial_timeout": 90.0
  }'
```

## Performance Tips

1. **Use Fast Mode** when possible - it's significantly faster than render mode
2. **Enable Auto Mode** - automatically chooses the best strategy
3. **Batch Requests** - process multiple URLs in a single API call
4. **Resource Limits** - configure Docker resource limits based on your workload
5. **Caching** - mount a persistent volume for browser cache

## Security Considerations

1. **Network Access** - The API needs internet access to fetch URLs
2. **Resource Limits** - Set appropriate CPU/memory limits to prevent abuse
3. **Rate Limiting** - Consider adding rate limiting in production
4. **Authentication** - Add authentication layer for production deployments
5. **HTTPS** - Use reverse proxy (nginx) with SSL in production

## License

MIT License - See LICENSE file for details
