# =============================================================================
# Dockerfile — VIIRS primary productivity Cloud Run job
#
# Builds a single image used by two Cloud Run jobs:
#   viirs-netpp-nrt-daily   (JOB_MODE=nrt_daily)
#   viirs-netpp-sq-sweep    (JOB_MODE=sq_sweep)
#
# The entrypoint reads JOB_MODE and calls control_viirs_netpp.py accordingly.
# =============================================================================

FROM python:3.11-slim

# Limit numpy/scipy thread oversubscription inside a container
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        curl \
        gnupg \
        ca-certificates \
        libhdf5-dev \
        libnetcdf-dev \
        netcdf-bin \
        nco \
    && rm -rf /var/lib/apt/lists/*
# ---------------------------------------------------------------------------
# Google Cloud SDK  (for gsutil used inside make_viirs_netpp.py)
# ---------------------------------------------------------------------------
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
        https://packages.cloud.google.com/apt cloud-sdk main" \
        | tee /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
COPY config/requirements.txt ./config/
RUN pip install --no-cache-dir -r config/requirements.txt

# ---------------------------------------------------------------------------
# Application source
# ---------------------------------------------------------------------------
COPY config/config.yml      ./config/config.yml
COPY templates/              ./templates/
COPY scripts/               ./scripts/
COPY entrypoint.sh          ./entrypoint.sh

RUN chmod +x entrypoint.sh scripts/control_viirs_netpp.py scripts/make_viirs_netpp.py

# ---------------------------------------------------------------------------
# Runtime environment
# ---------------------------------------------------------------------------
ENV PYTHONPATH="/app:/app/scripts" \
    PYTHONUNBUFFERED=1 \
    NPP_LOCAL_SCRATCH="/tmp/npp_scratch" \
    HOME="/tmp" \
    NPP_NCO_DIR="/usr/bin"

RUN mkdir -p /tmp/npp_scratch

ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]