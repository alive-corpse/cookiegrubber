# syntax=docker/dockerfile:1

FROM alpine:edge

RUN apk add --no-cache \
    python3 \
    py3-pip \
    curl \
    tini

WORKDIR /app

COPY requirements.txt .

RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

RUN mkdir -p /data/cookies && chown -R appuser:appgroup /app /data

USER appuser

ENTRYPOINT ["tini", "--"]

CMD ["python3", "main.py"]
