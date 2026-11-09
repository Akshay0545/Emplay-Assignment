# RFP Intelligence API image. Build from the repository root:
#   docker build -t rfp-intel-api .
# The local embedding and re-ranking models are downloaded on first start into
# /root/.cache/huggingface (mount a volume to keep them between restarts).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY config ./config
COPY rfp_intel ./rfp_intel
COPY eval ./eval

EXPOSE 8001
CMD ["uvicorn", "rfp_intel.api.server:app", "--host", "0.0.0.0", "--port", "8001"]
