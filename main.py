import re
from pathlib import Path
from typing import List

from celery import Celery

from pydantic import BaseSettings

from camera.gpio import Camera

file_save_re = re.compile(r'Saving file as (.*)')


class Settings(BaseSettings):
    name: str
    port: str
    pin: int
    files: List[Path] = list()

    class Config:
        env_file = '.env'
        env_prefix = 'pocs_'


# Create settings from env vars.
app_settings = Settings()
cam = Camera(name=app_settings.name, port=app_settings.port, pin=app_settings.pin)

# Start celery.
app = Celery()
app.config_from_object('celeryconfig')


@app.task(name='camera.status', bind=True)
def status(self):
    """Get the status of the camera."""
    self.update_state(state='STATUS', meta={'status': str(cam)})
    return dict(status=str(cam))


@app.task(name='camera.open_shutter', bind=True)
def open_shutter(self):
    """Open the camera shutter."""
    self.update_state(state='OPENING', meta={'status': str(cam)})
    cam.open_shutter()
    self.update_state(state='OPEN', meta={'status': str(cam)})
    return dict(status=str(cam))


@app.task(name='camera.close_shutter', bind=True)
def close_shutter(self):
    """Close the camera shutter."""
    self.update_state(state='CLOSING', meta={'status': str(cam)})
    cam.close_shutter()
    self.update_state(state='CLOSED', meta={'status': str(cam)})
    return dict(status=str(cam))


@app.task(name='camera.start_tether', bind=True)
def start_tether(self, output_dir: str):
    """Start the camera tether."""
    cam.start_tether(output_dir=Path(output_dir))
    self.update_state(state='TETHERED', meta={'status': str(cam), 'output_dir': output_dir})
    return dict(status=str(cam))


@app.task(name='camera.stop_tether', bind=True)
def stop_tether(self):
    """Stop the camera tether."""
    cam.stop_tether()
    self.update_state(state='UNTETHERED', meta={'status': str(cam)})

    tethered_files = app_settings.files.copy()
    app_settings.files.clear()
    return dict(status=str(cam), files=tethered_files)


@app.task(name='camera.file_list', bind=True)
def file_list(self):
    """Get the file list from the camera."""
    if cam.is_tethered:
        for line in cam.tether_process.stdout.readlines():
            file_match = file_save_re.match(line)
            if file_match is not None:
                app_settings.files.append(file_match.group(1).strip())

    return dict(files=app_settings.files)
