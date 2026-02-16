# Security Policy

## Supported Versions

We release security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| 0.5.x   | :white_check_mark: |
| 0.4.x   | :x:                |
| < 0.4   | :x:                |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them responsibly via one of these methods:

### Preferred Method: Private Security Advisory
1. Go to https://github.com/seifreed/MarkDownIngress/security/advisories
2. Click "Report a vulnerability"
3. Fill in the details

### Alternative: Email
Send an email to **mriverolopez@gmail.com** with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### What to Expect
- **Acknowledgment** - Within 48 hours
- **Initial assessment** - Within 7 days
- **Fix timeline** - Depends on severity
  - Critical: 24-72 hours
  - High: 1-2 weeks
  - Medium/Low: Next release cycle

### Disclosure Policy
- We will work with you to understand and fix the issue
- We will credit you in the security advisory (unless you prefer to remain anonymous)
- We will coordinate public disclosure timing with you
- Typically 90 days after fix is released

---

## Security Considerations When Using MarkDownIngress

### 1. Untrusted Input
MarkDownIngress is designed to process **untrusted web content** safely:
- ✅ HTML is sanitized during extraction
- ✅ JavaScript is removed
- ✅ Hidden content is detected and flagged
- ✅ Prompt injection attempts are scored

However:
- ⚠️ Always review `injection_score` and `flags` before using content with LLMs
- ⚠️ Use `strict=True` mode for maximum safety
- ⚠️ Validate URLs before ingestion (avoid internal IPs, localhost)

### 2. Screenshots
When using screenshot capture:
- 🔒 Screenshots may contain sensitive information visible on the page
- 🔒 Store screenshots securely (encrypted storage, limited access)
- 🔒 Delete screenshots after use if they contain sensitive data
- 🔒 Be aware of compliance requirements (GDPR, data retention)

### 3. Metadata
Extracted metadata may include:
- Author names, emails (from meta tags)
- Publication dates
- Keywords/descriptions
- Review metadata for sensitive information before logging/storing

### 4. Network Security
- 🌐 MarkDownIngress makes HTTP/HTTPS requests to target URLs
- 🌐 Use HTTPS URLs when possible
- 🌐 Validate URLs before ingestion to prevent SSRF attacks
- 🌐 Consider using a proxy for isolation in production

### 5. Stealth Mode
- 🕵️ Stealth mode is for bypassing bot detection, not for malicious purposes
- 🕵️ Respect robots.txt and terms of service
- 🕵️ Use reasonable rate limiting
- 🕵️ Stealth features can be detected - use responsibly

### 6. API Server
When deploying the FastAPI server:
- 🔐 Use authentication (not included by default)
- 🔐 Implement rate limiting
- 🔐 Use HTTPS in production
- 🔐 Validate all inputs
- 🔐 Monitor for abuse
- 🔐 Consider resource limits (max URLs, timeout limits)

### 7. Dependencies
- 📦 Keep dependencies updated
- 📦 Review Playwright security advisories
- 📦 Monitor for CVEs in dependencies
- 📦 Use virtual environments to isolate

---

## Known Security Limitations

### Injection Detection
- Pattern-based detection can have false positives/negatives
- New injection techniques may not be detected
- Use as a signal, not absolute protection
- Combine with other security measures (output validation, content filtering)

### Web Scraping Risks
- Target sites may serve malicious content
- Large pages can cause resource exhaustion
- Infinite redirects can cause hangs (mitigated by timeouts)

### Playwright/Browser
- Running headless browsers has inherent security risks
- Browser exploits could affect the host system
- Use containerization (Docker) for isolation in production
- Keep Playwright and browsers updated

---

## Security Best Practices

### For Library Users
```python
from markdown_ingress import ingest

# ✅ Good: Validate URL
url = sanitize_url(user_input)
if not is_safe_url(url):
    raise ValueError("Invalid URL")

# ✅ Good: Use strict mode
doc = ingest(url, strict=True)

# ✅ Good: Check security flags
if doc.injection_score > 0.5:
    log_warning(f"High injection score: {doc.flags}")
    
# ✅ Good: Limit timeout
doc = ingest(url, timeout=30)

# ❌ Bad: Blindly trust all content
doc = ingest(untrusted_url)
send_to_llm(doc.markdown)  # No validation!
```

### For API Deployers
```python
# ✅ Good: Add authentication
@app.post("/ingest")
async def ingest_endpoint(
    request: IngestRequest,
    api_key: str = Depends(verify_api_key)
):
    ...

# ✅ Good: Rate limiting
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@app.post("/ingest")
@limiter.limit("10/minute")
async def ingest_endpoint(...):
    ...

# ✅ Good: Input validation
if not is_allowed_domain(request.url):
    raise HTTPException(403, "Domain not allowed")
```

---

## Security Checklist for Production

- [ ] Use latest stable version
- [ ] Enable strict mode by default
- [ ] Validate all URLs before ingestion
- [ ] Review injection_score and flags
- [ ] Implement authentication (API server)
- [ ] Add rate limiting (API server)
- [ ] Use HTTPS for API server
- [ ] Run in Docker container (isolation)
- [ ] Monitor resource usage
- [ ] Set reasonable timeouts
- [ ] Secure screenshot storage
- [ ] Review logs for sensitive data
- [ ] Keep dependencies updated
- [ ] Have incident response plan

---

## Responsible Disclosure Hall of Fame

We thank the following security researchers for responsible disclosure:

*(No reports yet)*

---

## Contact

For security concerns: **mriverolopez@gmail.com**

For general support: GitHub Issues

---

*This security policy was last updated: 2024-12*
