FROM python:3.11-slim

# System dependencies: LibreOffice, Tesseract, build tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        tesseract-ocr \
        gcc g++ \
        ffmpeg \
        && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home distill
WORKDIR /home/distill/app

# Copy source
COPY packages/core ./packages/core
COPY packages/app  ./packages/app

# Install Python packages from pyproject.toml
RUN pip install --no-cache-dir \
    "./packages/core[ocr,audio]" \
    "./packages/app"

# Switch to non-root user
USER distill

EXPOSE 7860

# Default: API server. Worker overrides via docker-compose command.
CMD ["uvicorn", "distill_app.server:app", "--host", "0.0.0.0", "--port", "7860"]
