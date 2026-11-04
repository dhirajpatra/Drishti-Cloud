FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 --no-create-home drishti
COPY agent.py .
USER drishti
CMD ["python", "-u", "agent.py"]
