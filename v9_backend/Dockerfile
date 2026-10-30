FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 gs
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY --chown=gs:gs application/ /app/
COPY --chown=gs:gs models/ /app/models/
COPY --chown=gs:gs MODEL_ARTIFACT_REGISTRY.json ORIGINAL_MODEL_RELEASE_REGISTRY.json MODEL_ACTIVATION_APPROVALS.json /app/
ENV GOOGLESAADA_MODEL_REGISTRY_PATH=/app/MODEL_ARTIFACT_REGISTRY.json \
    GOOGLESAADA_PHASE2_MODEL_REGISTRY=/app/ORIGINAL_MODEL_RELEASE_REGISTRY.json \
    GOOGLESAADA_PHASE2_APPROVALS=/app/MODEL_ACTIVATION_APPROVALS.json \
    GOOGLESAADA_ACTIVATION_MANIFEST_PATH=/app/MODEL_ACTIVATION_APPROVALS.json \
    PORT=7860
RUN python -m compileall -q /app && python -c "import sklearn; assert sklearn.__version__ == '1.9.1'"
USER gs
EXPOSE 7860
CMD ["sh","-c","python /app/gs_container_preflight.py && exec uvicorn app:app --host 0.0.0.0 --port 7860"]
