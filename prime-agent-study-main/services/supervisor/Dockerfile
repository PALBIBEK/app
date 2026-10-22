FROM python:3.12-slim
WORKDIR /app
COPY services/supervisor/pyproject.toml /app/pyproject.toml
COPY services/supervisor/supervisor /app/supervisor
RUN pip install --no-cache-dir -e ".[dev,docker,s3,postgres]"
ENV PYTHONPATH=/app
# One image, two roles: `api` (the default) or `worker` (python -m supervisor <role>).
# 8080 the API, 8090 the model gateway, 8092 the memory service (SERVE_* pick which);
# 8081 a worker's /healthz and /readyz (with WORKER_HEALTH_PORT=8081).
EXPOSE 8080 8081 8090 8092
ENTRYPOINT ["python", "-m", "supervisor"]
CMD ["api"]
