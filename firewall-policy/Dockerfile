FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=65534:65534 policy_service ./policy_service
USER 65534:65534
EXPOSE 8081
CMD ["uvicorn", "policy_service.main:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "2"]
