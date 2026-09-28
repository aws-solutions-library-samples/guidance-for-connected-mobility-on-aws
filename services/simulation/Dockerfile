FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY simulation_api.py realtime_telemetry_simulator.py event_catalog_loader.py \
     event_catalog_driver.py can_encoder.py can_bus_writer.py gps_publisher.py \
     uds_dtc_responder.py sovd_payload_sizing.py uds_freeze_frame_fixtures.py \
     routine_sims.py ./
COPY can/ ./can/

# Overlay _shared/ from services/_shared/ so `import _shared.routine_catalog`
# resolves from /app/ (the WORKDIR). Staged into the build context at CDK synth
# time by _stage_sim_service_context() in simulation_stack.py (F28 fix: a safety
# module must ship in the container image, not only in deployment/scripts/).
# Source-of-truth is services/_shared/ — do NOT copy modules out of that package.
COPY _shared/ ./_shared/

ENV PYTHONUNBUFFERED=1
EXPOSE 5001

# Default: run API server. Override CMD for sim-worker tasks.
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "2", "--timeout", "300", "simulation_api:app"]
