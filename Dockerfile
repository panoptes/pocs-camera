FROM mambaorg/micromamba

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV PYTHONUNBUFFERED 1

ENV BASE_DIR=/images

WORKDIR /home/micromamba
ADD --chown=micromamba:micromamba https://raw.githubusercontent.com/gonzalo/gphoto2-updater/master/gphoto2-updater.sh .

USER root
RUN ls -l  && chmod +x gphoto2-updater.sh && \
    ./gphoto2-updater.sh --development && \
    apt-get autoremove --purge --yes && \
    apt-get autoclean --yes && \
    apt-get --yes clean && \
    rm -rf /var/lib/apt/lists/*

# Create image directory, and update permissions for usb.
RUN mkdir -p "${BASE_DIR}" && chmod 777 "${BASE_DIR}" && \
    mkdir -p /app && chmod 777 /app

USER micromamba
COPY --chown=micromamba:micromamba environment.yaml /tmp/environment.yaml
RUN micromamba install -y -n base -f /tmp/environment.yaml && \
    micromamba clean --all --yes

WORKDIR /app
USER "${USERNAME}"
COPY main.py .

EXPOSE 6565

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6565"]
