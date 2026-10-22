FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY unbiased ./unbiased
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "unbiased.web:app", "--host", "0.0.0.0", "--port", "8000"]
