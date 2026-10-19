FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 GRADIO_ANALYTICS_ENABLED=False
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
RUN python verify_staging_models.py
RUN python verify_staging_inference.py
RUN python verify_v8_integrated_runtime.py
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "googlesaada_v8_approved_entry:app", "--host", "0.0.0.0", "--port", "7860"]
