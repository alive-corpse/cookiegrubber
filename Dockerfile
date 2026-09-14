# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build - compile binary with pyinstaller
# ---------------------------------------------------------------------------
FROM alpine:edge AS builder

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-setuptools \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    curl \
    tini

WORKDIR /build

COPY requirements.txt .

RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt && \
    pip3 install --no-cache-dir --break-system-packages pyinstaller

COPY main.py .

RUN pyinstaller \
    --onefile \
    --clean \
    --name cookiegrubber \
    --noconfirm \
    main.py

# ---------------------------------------------------------------------------
# Stage 2: runtime - minimal image with binary only
# ---------------------------------------------------------------------------
FROM alpine:edge AS runtime

RUN apk add --no-cache \
    curl \
    tini

WORKDIR /app

COPY --from=builder /build/dist/cookiegrubber /usr/local/bin/cookiegrubber
RUN chmod +x /usr/local/bin/cookiegrubber

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

RUN mkdir -p /data/cookies && chown -R appuser:appgroup /data

USER appuser

ENTRYPOINT ["tini", "--"]
CMD ["cookiegrubber"]
