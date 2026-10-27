FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY reporta_cdmx.py .
COPY examples ./examples

# Run the web server (automatically listens on $PORT or 8000)
CMD ["python", "reporta_cdmx.py"]
