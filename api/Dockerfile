FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ripgrep openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py index.html dashboard.html .

EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
