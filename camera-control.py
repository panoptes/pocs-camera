import celery
import typer
from settings import State, Settings, Camera, AppSettings

# Create settings from env vars.
settings = Settings()

# Build app settings.
app_settings = AppSettings(
    camera=Camera(name=settings.camera_name,
                  port=settings.camera_port,
                  pin=settings.camera_pin),
    celery=dict(broker_url=settings.broker_url,
                result_backend=settings.result_backend),
)

# Celery app.
celery_app = celery.Celery()
celery_app.config_from_object(app_settings.celery)

# CLI app.
typer_app = typer.Typer()


@typer_app.command()
def command(command_list):
    """Runs a generic command on the camera."""
    full_command = ['--port', app_settings.camera.port, *command_list]
    task = celery_app.send_task('camera.command', args=full_command)
    return celery_app.AsyncResult(task.id)


@typer_app.command()
def get_property(prop_name):
    """Gets a property from the camera."""
    full_command = ['--port', app_settings.camera.port, f'--get-property {prop_name}']
    task = celery_app.send_task('camera.command', args=full_command)
    return celery_app.AsyncResult(task.id)
