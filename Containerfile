# Podman / Docker compatible image of the PK analysis demonstrator.
#   podman build -t pk-analysis-poc -f Containerfile .
#   podman run --rm --network none -v ./runs:/app/runs pk-analysis-poc
# Same image + same manifest -> same results.
FROM docker.io/rocker/r-ver:4.6.1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Python for the orchestrator; a C toolchain for rxode2's run-time model compilation.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv build-essential gfortran \
 && rm -rf /var/lib/apt/lists/*

COPY install_r_packages.R /tmp/install_r_packages.R
RUN Rscript /tmp/install_r_packages.R && rm -rf /tmp/downloaded_packages /tmp/Rtmp*

COPY requirements.txt requirements-llm.txt /tmp/
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/requirements-llm.txt

# Generic BLAS kernels and libm code paths, so results do not depend on the host CPU (engine/runner.py).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHON=/opt/venv/bin/python \
    PKPOC_RUNS_DIR=/app/runs \
    OPENBLAS_CORETYPE=Prescott \
    OPENBLAS_NUM_THREADS=1 \
    GLIBC_TUNABLES=glibc.cpu.hwcaps=-AVX2_Usable,-AVX512F_Usable,-FMA_Usable,-FMA4_Usable,-AVX2,-AVX512F,-FMA,-FMA4

WORKDIR /app
COPY . /app

LABEL org.opencontainers.image.source="https://github.com/djindetoss/pk-analysis-poc" \
      org.opencontainers.image.description="AI-assisted population-PK analysis demonstrator (synthetic data only)" \
      org.opencontainers.image.licenses="EUPL-1.2"

CMD ["./run_demo.sh", "--auto-confirm"]
