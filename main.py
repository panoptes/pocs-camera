from celery import Celery

from pydantic import BaseSettings

from camera.gpio import Camera


class Settings(BaseSettings):
    name: str
    port: str
    pin: int

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
    self.update_state(state='PROGRESS', meta={'status': str(cam)})
    return dict(status=str(cam))
