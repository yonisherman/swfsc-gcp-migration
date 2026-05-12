# --- Stage 1: Build DINEOF inside seadas image to match GLIBC ---
FROM seadas/ocssw-run:1.1 AS builder
ENV DEBIAN_FRONTEND=noninteractive

USER root
RUN apt-get update && apt-get install -y \
    build-essential gfortran git wget \
    libnetcdf-dev libnetcdff-dev libopenblas-dev libarpack2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone https://github.com/Aida-Alvera/DINEOF && \
    cd DINEOF && \
    cp config.mk.template config.mk && \
    make

# --- Stage 2: Runner ---
FROM seadas/ocssw-run:1.1

ENV DEBIAN_FRONTEND=noninteractive
USER root
WORKDIR /app

# RESTORED FROM Dockerfile_og: Correct PATH for NASA tools
# This is what allows 'obdaac_download' to be found by the system
ENV OCSSWROOT=/root/ocssw
ENV PATH="/app/src/DINEOF:/opt/cwutils/bin:${OCSSWROOT}/bin:${OCSSWROOT}/bin/scripts:${PATH}"
ENV PYTHONPATH="/app:/app/scripts"
ENV PYTHONUNBUFFERED=1
ENV HOME="/tmp"
ENV CONFIG_PATH="/app/config/config.yaml"

# System dependencies + Python (Aligned with Dockerfile_og)
RUN apt-get update && apt-get install -y \
    wget curl gnupg dos2unix jq perl git \
    libopenblas0 libarpack2 libnetcdff-dev libgfortran5 \
    libnetcdf-dev libquadmath0 nco netcdf-bin \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Google Cloud CLI (Aligned with Dockerfile_og)
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | \
    tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | \
    gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update && apt-get install -y google-cloud-cli && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY config/requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Project source
COPY . .

# DINEOF binary from Stage 1
RUN mkdir -p /app/src/DINEOF
COPY --from=builder /build/DINEOF/dineof /app/src/DINEOF/dineof
RUN chmod +x /app/src/DINEOF/dineof

# CWUtils
RUN wget https://www.star.nesdis.noaa.gov/socd/coastwatch/cwf/cwutils-4_1_0_397-linux-x86_64.tar.gz && \
    mkdir -p /opt/cwutils && \
    tar -xzf cwutils-4_1_0_397-linux-x86_64.tar.gz -C /opt/cwutils --strip-components=1 && \
    rm cwutils-4_1_0_397-linux-x86_64.tar.gz

# Ensure entrypoint is executable and has correct line endings
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]