FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 GRADIO_ANALYTICS_ENABLED=False
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "googlesaada_unified_app:app", "--host", "0.0.0.0", "--port", "7860"]
