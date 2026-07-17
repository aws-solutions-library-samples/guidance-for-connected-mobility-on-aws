FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY simulation_api.py realtime_telemetry_simulator.py event_catalog_loader.py \
     event_catalog_driver.py can_encoder.py can_bus_writer.py gps_publisher.py \
     uds_dtc_responder.py ./
COPY can/ ./can/

ENV PYTHONUNBUFFERED=1
EXPOSE 5001

# Default: run API server. Override CMD for sim-worker tasks.
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "2", "--timeout", "300", "simulation_api:app"]
