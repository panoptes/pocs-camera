FROM python:3-slim

ENV USERNAME=panoptes
ENV PORT=6565
ENV BASE_DIR=/images

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      gphoto2 python3-uvicorn

# Create user, image directory, and update permissions for usb.
RUN useradd --no-create-home -G plugdev ${USERNAME} && \
    mkdir -p "${BASE_DIR}" && chmod 777 "${BASE_DIR}" && \
    mkdir -p /app && chmod 777 /app

COPY requirements.txt .
RUN pip install -r requirements.txt

WORKDIR /app
USER "${USERNAME}"
COPY main.py .

EXPOSE "${PORT}"

CMD uvicorn main:app --host 0.0.0.0 --port "${PORT}"