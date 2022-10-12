import celery
import typer

# Celery app.
celery_app = celery.Celery()
celery_app.config_from_object('celeryconfig')

# CLI app.
typer_app = typer.Typer()


@typer_app.command()
def status():
    """Get camera status."""
    task = celery_app.send_task('camera.status')
    result = task.get(timeout=20)
    typer.echo(f'Result: {result}')


if __name__ == '__main__':
    typer_app()
