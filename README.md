<div align="center">
  <h1 align="center">isoAutomate Python SDK</h1>
  
  <p align="center">
    <b>The Sovereign Browser Infrastructure & Orchestration Platform</b>
  </p>

  <a href="https://pypi.org/project/isoautomate/">
    <img src="https://img.shields.io/pypi/v/isoautomate.svg?color=blue" alt="PyPI version">
  </a>
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  </a>
  <a href="https://isoautomate.com/docs">
    <img src="https://img.shields.io/badge/Docs-isoautomate.com-orange.svg" alt="Documentation">
  </a>
</div>

<br />

<div align="center">
<img src="ext/sdk-python.png" alt="isoAutomate Architecture" width="450" />
</div>

---

## Introduction

The **isoAutomate Python SDK** is a production-grade client that acts as a **remote control** for browsers running on **isoFleet**, isoAutomate’s Redis-backed browser orchestration infrastructure.

Your **Python code runs locally**, while **real browsers run remotely** inside fully isolated, disposable containers.

This architecture enables:

- Deterministic automation
- Strong isolation (no cross-session leaks)
- Scalable browser fleets
- Native video, screenshots, MFA, and persistence

The SDK communicates with isoFleet over Redis, orchestrating browser lifecycle, execution, and artifacts.

---

## Installation

```bash
pip install isoautomate
```

## Configuration

The SDK requires a Redis connection to communicate with isoFleet.
You may configure it using environment variables or direct initialization.

### Method 1: Environment Variables (.env)

**Option A: Single Redis URL**
```ini
REDIS_URL=rediss://:password@host:port/0
```

**Option B: Individual Fields**
```ini
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=yourpassword
REDIS_DB=0
REDIS_SSL=true
```

### Method 2: Direct Initialization

**Using redis_url**
```python
from isoautomate import BrowserClient

browser = BrowserClient(
    redis_url="rediss://:password@host:port/0"
)
```

**Using Individual Arguments**
```python
from isoautomate import BrowserClient

browser = BrowserClient(
    redis_host="localhost",
    redis_port=6379,
    redis_password="yourpassword",
    redis_db=0,
    redis_ssl=True
)
```

## Usage Examples

### Context Manager (Recommended)

```python
from isoautomate import BrowserClient

with BrowserClient() as browser:
    # Acquire a session with recording enabled
    browser.acquire(browser_type="chrome", record=True)
    
    browser.open_url("https://example.com")
    browser.assert_text("Example Domain")

# Browser is automatically released here
print(browser.video_url)
print(browser.session_data)
```

### Manual Control

```python
from isoautomate import BrowserClient

browser = BrowserClient()
try:
    browser.acquire(record=True)
    browser.open_url("https://example.com")
    browser.assert_text("Example Domain")
finally:
    browser.release()
```

## Core Features

### Commercial Assertions

```python
browser.assert_text("Checkout Complete")
```
Failure screenshots are saved to:

```text
screenshots/failures/
```

### Video Recording

```python
browser.acquire(record=True)
browser.open_url("https://example.com")
browser.release()
print(browser.video_url)
```
### MFA (Multi-Factor Authentication)

```python
code = browser.get_mfa_code("YOUR_TOTP_SECRET")
print(code)
```
### Cookies

```python
browser.save_cookies("cookies.json")
browser.load_cookies("cookies.json")
```
### File Uploads

```python
browser.upload_file(
    selector="input[type='file']",
    file_path="./document.pdf"
)
```
### Stealth & Low-Level Control

```python
ua = browser.get_user_agent()
print(ua)

browser.evaluate("navigator.webdriver")
```
## Build & Publish Instructions

```bash
pip install build twine
python -m build
twine upload dist/*
```
## License

MIT License

Copyright (c) 2026 isoAutomate