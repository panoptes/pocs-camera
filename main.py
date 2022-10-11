from celery import Celery

from pydantic import BaseSettings
from panoptes.utils.library import load_module


class Settings(BaseSettings):
    name: str
    port: str
    pin: int
    camera_class: str = "camera.gphoto2.Camera"

    class Config:
        env_file = '.env'
        env_prefix = 'pocs_'


# Create settings from env vars.
app_settings = Settings()
cam = load_module(app_settings.camera_class)(**app_settings.dict())

# Start celery.
app = Celery()
app.config_from_object('celeryconfig')


@app.task(name='camera.status', bind=True)
def status(self):
    """Get the status of the camera."""
    self.update_state(state='PROGRESS', meta={'status': str(cam)})
    return dict(status=str(cam))
