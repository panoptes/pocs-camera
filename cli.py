import celery
import typer

# Celery app.
celery_app = celery.Celery()
celery_app.config_from_object('celeryconfig')

# CLI app.
typer_app = typer.Typer()


@typer_app.command('status')
def status():
    """Get camera status."""
    task = celery_app.send_task('camera.status')
    result = task.get(timeout=20)
    typer.echo(f'Result: {result}')


@typer_app.command('start-tether')
def start_tether():
    """Start camera tether."""
    task = celery_app.send_task('camera.start_tether')
    typer.echo(f'Task: {task.id=}')


@typer_app.command('stop-tether')
def stop_tether():
    """Stop camera tether."""
    task = celery_app.send_task('camera.stop_tether')
    typer.echo(f'Stopping tether: {task.id=}')
    result = task.get(timeout=20)


if __name__ == '__main__':
    typer_app()
