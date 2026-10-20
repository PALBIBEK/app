FROM python:3.12-slim
WORKDIR /app
COPY services/memory/pyproject.toml /app/pyproject.toml
COPY services/memory/memory_service /app/memory_service
RUN pip install --no-cache-dir -e ".[dev,postgres]"
ENV PYTHONPATH=/app
EXPOSE 8092
CMD ["python", "-m", "memory_service"]
