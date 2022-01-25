FROM python:3.10-slim-buster

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV PYTHONUNBUFFERED 1

ENV USERNAME=panoptes
ENV WORK_DIR=/app
ENV BASE_DIR=/images

ADD https://raw.githubusercontent.com/gonzalo/gphoto2-updater/master/gphoto2-updater.sh .

RUN chmod +x gphoto2-updater.sh && \
    ./gphoto2-updater.sh --development && \
    apt-get autoremove --purge --yes && \
    apt-get autoclean --yes && \
    apt-get --yes clean && \
    rm -rf /var/lib/apt/lists/*

# Create image directory, and update permissions for usb.
RUN useradd --no-create-home -G plugdev ${USERNAME} && \
    mkdir -p "${BASE_DIR}" && chmod 777 "${BASE_DIR}" && \
    mkdir -p "$WORK_DIR" && chmod 777 "$WORK_DIR"

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

WORKDIR "$WORK_DIR"
USER "${USERNAME}"
COPY gphoto2.py .

EXPOSE 6565

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6565"]
