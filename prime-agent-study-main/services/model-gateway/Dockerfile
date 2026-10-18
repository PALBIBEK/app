FROM python:3.12-slim
WORKDIR /app
COPY services/model-gateway/pyproject.toml /app/pyproject.toml
COPY services/model-gateway/src /app/src
RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/app/src
ENV PORT=8090
EXPOSE 8090
CMD ["uvicorn", "model_gateway.app:app", "--host", "0.0.0.0", "--port", "8090"]
