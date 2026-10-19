FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
ENV PYTHONUNBUFFERED=1 PORT=7860
EXPOSE 7860
CMD ["python", "backend/api_app.py"]
