# Multi-stage build for MarkDownIngress API Server
# Stage 1: Base image with system dependencies
FROM python:3.13-slim AS base

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Stage 2: Dependencies
FROM base AS dependencies

# Copy dependency files
COPY pyproject.toml /app/

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir build && \
    pip install --no-cache-dir \
    "httpx>=0.27.0" \
    "selectolax>=0.3.21" \
    "readability-lxml>=0.8.1" \
    "markdownify>=0.12.1" \
    "tiktoken>=0.7.0" \
    "PyYAML>=6.0" \
    "rich>=14.0" \
    "playwright>=1.43.0" \
    fastapi \
    "uvicorn[standard]" \
    pydantic

# Install Playwright browsers (Chromium only for smaller image)
RUN playwright install --with-deps chromium

# Stage 3: Application
FROM base AS application

# Copy installed packages from dependencies stage
COPY --from=dependencies /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin
COPY --from=dependencies /root/.cache/ms-playwright /root/.cache/ms-playwright

# Copy application code
COPY markdown_ingress/ /app/markdown_ingress/
COPY pyproject.toml /app/
COPY README.md /app/
COPY LICENSE /app/

# Install the package in editable mode
RUN pip install --no-cache-dir -e .

# Create cache directory
RUN mkdir -p /app/cache

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8000/health || exit 1

# Run the server
CMD ["uvicorn", "markdown_ingress.api_server:app", "--host", "0.0.0.0", "--port", "8000"]
