FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN python -m pip install --no-cache-dir '.[gateway]'
USER 1000:1000
CMD ["python", "-m", "aec_cloud_gateway.gateway"]
