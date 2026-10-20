FROM python:3.12-slim
WORKDIR /app
COPY services/supervisor/pyproject.toml /app/pyproject.toml
COPY services/supervisor/supervisor /app/supervisor
RUN pip install --no-cache-dir -e ".[dev,docker,s3,postgres]"
ENV PYTHONPATH=/app
# 8080 the API, 8090 the model gateway, 8092 the memory service (SERVE_* pick which).
EXPOSE 8080 8090 8092
CMD ["python", "-m", "supervisor.main"]
