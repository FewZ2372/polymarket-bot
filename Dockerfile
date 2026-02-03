# Polymarket Trading Bot
# Multi-stage build for smaller image size

FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production image
FROM python:3.11-slim

WORKDIR /app

# Create non-root user for security
RUN useradd -m -u 1000 botuser

# Copy installed packages from builder to a location accessible by all users
COPY --from=builder /root/.local /home/botuser/.local

# Make sure scripts in .local are usable
ENV PATH=/home/botuser/.local/bin:$PATH
ENV PYTHONPATH=/home/botuser/.local/lib/python3.11/site-packages:$PYTHONPATH

# Copy application code
COPY *.py ./

# Set ownership
RUN chown -R botuser:botuser /app /home/botuser/.local
USER botuser

# Expose port for healthcheck
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=5)" || exit 1

# Run the bot
CMD ["python", "main.py"]
