FROM python:3.12-slim
WORKDIR /app
COPY services/supervisor/pyproject.toml /app/pyproject.toml
COPY services/supervisor/src /app/src
RUN pip install --no-cache-dir -e ".[dev,docker,s3,postgres]"
ENV PYTHONPATH=/app/src
EXPOSE 8080
CMD ["python", "-m", "supervisor.main"]
