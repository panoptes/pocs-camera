import celery
import typer

# Celery app.
celery_app = celery.Celery()
celery_app.config_from_object('celeryconfig')

# CLI app.
typer_app = typer.Typer()


@typer_app.command()
def status(command_list):
    """Get camera status."""
    task = celery_app.send_task('camera.status')
    return celery_app.AsyncResult(task.id)


if __name__ == '__main__':
    typer_app()
