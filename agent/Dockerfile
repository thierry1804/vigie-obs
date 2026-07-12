FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get purge -y npm && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ /app/agent/
COPY discovery/ /app/discovery/
COPY config/templates/ /app/config/templates/
COPY cli/ /app/cli/

ENV PYTHONPATH=/app
ENV VIGIE_DATA_DIR=/data
ENV VIGIE_MOCK_LLM=0
# Service headless conteneurisé : autorise le CLI Claude Code à s'exécuter en
# root avec bypassPermissions (sinon refus « cannot be used with root/sudo »).
ENV IS_SANDBOX=1

RUN mkdir -p /data

EXPOSE 8080
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8080"]
