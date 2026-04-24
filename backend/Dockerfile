FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps for: pip compilation (build-essential, libpq-dev, libxml2, libxslt),
# PDF/report libs pulled by maigret (libcairo2-dev, libpango*, libgdk-pixbuf, libffi),
# ExifTool for image metadata, curl/ca-certificates.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        libimage-exiftool-perl \
        build-essential \
        pkg-config \
        libpq-dev \
        libxml2 \
        libxml2-dev \
        libxslt1.1 \
        libxslt1-dev \
        libcairo2 \
        libcairo2-dev \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        libssl-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install -e .

# Install OSINT CLI tools from PyPI. All free/open source.
RUN pip install \
        sherlock-project \
        maigret \
        holehe

COPY app /app/app

RUN mkdir -p /data/cache /data/exports

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
