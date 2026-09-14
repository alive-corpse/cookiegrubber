# cookiegrubber

Export cookies from a remote Chromium browser via Chrome DevTools Protocol (CDP).

Provides two modes:
- **HTTP API** — REST endpoint for fetching cookies on demand
- **CLI** — one-shot export to Netscape cookie files

## Main features

- Connects to any remote Chromium instance via CDP (WebSocket)
- Filters cookies by domain (supports comma-separated list)
- Two output formats: Netscape (for yt-dlp, curl) and JSON
- Docker-based deployment with docker-compose
- Health check endpoint for container orchestration
- Per-domain cookie files with auto-generated timestamps

## Installation, configuration and running

### Prerequisites

- Docker and docker-compose installed
- A Chromium browser running with remote debugging enabled (`--remote-debugging-port=9222`)

### Configuration

1. Copy the environment file:

```
cp .env.example .env
```

2. Edit `.env`:

```
CDP_HOST=192.168.1.100       # IP/hostname of the remote Chromium
CDP_PORT=9222                 # CDP port on the remote Chromium
API_HOST=0.0.0.0              # Bind address for the API server
API_PORT=8876                 # Port to expose the API server
CLI_DOMAINS=                  # Comma-separated domains for CLI mode (leave empty for API mode)
```

3. Build and start:

```
docker compose build
docker compose up
```

### API mode (default)

When `CLI_DOMAINS` is empty in `.env`, the container runs as an HTTP API server.

Available endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/cookies?cdp=HOST:PORT&domains=youtube.com&format=netscape|json` | Fetch cookies |

Examples:

```bash
# Cookies in Netscape format (default)
curl "http://localhost:8876/cookies?cdp=192.168.1.100:9222&domains=youtube.com"

# Cookies in JSON format
curl "http://localhost:8876/cookies?cdp=192.168.1.100:9222&domains=youtube.com&format=json"

# All cookies (no domain filter)
curl "http://localhost:8876/cookies?cdp=192.168.1.100:9222"

# Use the default CDP endpoint set at startup
# Start with: --cdp 192.168.1.100:9222, then:
curl "http://localhost:8876/cookies?domains=youtube.com"
```

### CLI mode

When `CLI_DOMAINS` is set (non-empty) in `.env`, the container runs a one-shot export:

```
CLI_DOMAINS=youtube.com,google.com
```

This creates separate cookie files per domain inside the container:
- `youtube.com.cookies.YYYYMMDD_HHMMSS`
- `google.com.cookies.YYYYMMDD_HHMMSS`

### Running locally (without Docker)

```
pip install -r requirements.txt
python main.py --cdp 192.168.1.100:9222 --domains youtube.com
python main.py --api 0.0.0.0:8876 --cdp 192.168.1.100:9222
```

## Project structure

```
.
├── Dockerfile              # Docker image definition (Alpine Edge)
├── docker-compose.yml      # Container orchestration
├── .env.example            # Environment variables template
├── .env                    # Environment variables (local)
├── .dockerignore           # Files to exclude from Docker build
├── main.py                 # Application source code
├── requirements.txt        # Python dependencies
└── .venv/                  # Local virtual environment
```

## Author

Evgeniy Shumilov
- evgeniy.shumilov@gmail.com
- eashumilov@ya.ru

## Known issues

- Requires Chromium to be started with `--remote-debugging-port=PORT`
- Some cookies may require `--enable-features=NetworkService` in Chromium
