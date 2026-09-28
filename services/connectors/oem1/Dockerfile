# OEM1 connector — production-runnable Phase D1.
# Runs main.py which streams from Autonomic Feed Service and emits canonical JSON.
# D2 will replace stdout emit with a Kafka producer; this Dockerfile stays.
FROM python:3.13-slim AS runtime

# Unbuffered stdout/stderr so CloudWatch sees logs in real time (block-buffering
# in container pipes hides per-event progress otherwise).
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for grpcio (needs gcc + python-dev for some wheel builds)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps first for layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-input -r /app/requirements.txt

# Application code
COPY . /app/

# Run as non-root for defense-in-depth
RUN useradd --system --uid 10001 --no-create-home --shell /bin/false oem1 \
    && chown -R oem1:oem1 /app
USER oem1

# Default entrypoint runs main.py; CMD overridable for diagnostic shell-out
ENTRYPOINT ["python3", "main.py"]
