# --- Stage 1: Base Environment ---
# Python 3.10 is used to preserve compatibility with the legacy scientific
# Python stack required by the fronts and monthly-composite scripts.
FROM python:3.10-slim


# Limit threaded math libraries so parallelism stays predictable in Cloud Run.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1


# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget curl gnupg dos2unix jq perl \
    gdal-bin libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Install yq (portable version for bash config parsing)
RUN wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/bin/yq && \
    chmod +x /usr/bin/yq

# Install Google Cloud SDK for gcloud storage operations.
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | \
    tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | \
    gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update && apt-get install -y google-cloud-cli

WORKDIR /app

# --- Stage 2: Dependencies ---
COPY config/requirements.txt ./config/
RUN pip install --no-cache-dir -r config/requirements.txt
ENV PATH="/root/.local/bin:${PATH}"

# --- Stage 3: Source Code ---
COPY . .

COPY config/config.yml /app/config/config.yml


# Convert scripts to Unix format and make them executable
RUN dos2unix entrypoint.sh \
    scripts/mur_v41_downloader_dailyproc.sh \
    scripts/mur_v42_downloader.sh \
    scripts/MonthlyProc/MUR41_MonProc.sh \
    && chmod +x entrypoint.sh \
    scripts/mur_v41_downloader_dailyproc.sh \
    scripts/mur_v42_downloader.sh \
    scripts/MonthlyProc/MUR41_MonProc.sh

# Set Environment Variables
ENV PYTHONPATH="/app:/app/src:/app/scripts:/app/scripts/MonthlyProc" \
    PYTHONUNBUFFERED=1 \
    HOME="/tmp"

RUN mkdir -p /config

ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]