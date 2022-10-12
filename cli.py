import celery
import typer

# Celery app.
celery_app = celery.Celery()
celery_app.config_from_object('celeryconfig')

# CLI app.
typer_app = typer.Typer()


@typer_app.command('status')
def status(camera: str = typer.Argument(..., help='The name of the camera.')):
    """Get camera status."""
    task = celery_app.send_task('camera.status', queue=camera)
    result = task.get(timeout=20)
    typer.echo(f'Result: {result}')


@typer_app.command('open-shutter')
def open_shutter(camera: str = typer.Argument(..., help='The name of the camera.')):
    """Open camera shutter."""
    task = celery_app.send_task('camera.open_shutter', queue=camera)
    result = task.get(timeout=20)
    typer.echo(f'Result: {result}')


@typer_app.command('close-shutter')
def close_shutter(camera: str = typer.Argument(..., help='The name of the camera.')):
    """Close camera shutter."""
    task = celery_app.send_task('camera.close_shutter', queue=camera)
    result = task.get(timeout=20)
    typer.echo(f'Result: {result}')


@typer_app.command('start-tether')
def start_tether(camera: str = typer.Argument(..., help='The name of the camera.')):
    """Start camera tether."""
    task = celery_app.send_task('camera.start_tether', queue=camera)
    typer.echo(f'Task: {task.id=}')


@typer_app.command('stop-tether')
def stop_tether(camera: str = typer.Argument(..., help='The name of the camera.')):
    """Stop camera tether."""
    task = celery_app.send_task('camera.stop_tether', queue=camera)
    typer.echo(f'Stopping tether: {task.id=}')
    result = task.get(timeout=20)
    typer.echo(f'Result: {result}')


if __name__ == '__main__':
    typer_app()
