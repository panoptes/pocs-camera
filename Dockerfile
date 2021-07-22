FROM python:3-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gphoto2 &&\
    apt-get clean autoclean

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000

COPY . .

CMD python3 -m uvicorn main:app --host 0.0.0.0 --port $PORT
