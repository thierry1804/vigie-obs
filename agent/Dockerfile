FROM python:3.12-slim
WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ /app/agent/
COPY discovery/ /app/discovery/
COPY config/templates/ /app/config/templates/
COPY cli/ /app/cli/

ENV PYTHONPATH=/app
ENV VIGIE_DATA_DIR=/data
ENV VIGIE_MOCK_LLM=0

RUN mkdir -p /data

EXPOSE 8080
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8080"]
