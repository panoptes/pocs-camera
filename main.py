from datetime import datetime as dt
from pathlib import Path
from typing import List

from anyio import sleep
from fastapi import FastAPI
from pydantic import BaseModel, DirectoryPath

from camera import Camera, GphotoCommand


class Observation(BaseModel):
    sequence_id: str
    exptime: List[float] | float
    field_name: str = ''
    num_exposures: int = 1
    readout_time: float = 0.25

    @property
    def image_dir(self) -> Path:
        return Path(self.field_name) / self.sequence_id.replace('_', '/')


class AppSettings(BaseModel):
    camera: Camera | None = None
    base_dir: DirectoryPath = Path('.')
    is_observing: bool = False
    observation: Observation | None = None

    @property
    def image_dir(self) -> Path:
        image_dir = self.base_dir
        if self.observation is not None:
            image_dir = image_dir / self.observation.image_dir

        image_dir.mkdir(parents=True, exist_ok=True)
        return image_dir

    class Config:
        arbitrary_types_allowed = True


app_settings = AppSettings()
app = FastAPI()


@app.on_event('startup')
async def startup_tasks():
    """Set up the camera. """
    print('Starting up...')
    app_settings.camera = Camera()
    print(f'{app_settings=}')


@app.on_event('shutdown')
async def shutdown_tasks():
    await app_settings.camera.stop_tether()


@app.post('/take-sequence')
async def take_sequence(observation: Observation):
    """Take a sequence of exposures."""
    if app_settings.is_observing:
        return dict(success=False, message=f'Observation already in progress')
    else:
        app_settings.is_observing = True

    await app_settings.camera.start_tether(app_settings.image_dir)

    obs_start_time = dt.utcnow()
    await app_settings.camera.take_sequence(observation.exptime,
                                            observation.num_exposures,
                                            observation.readout_time)

    # Wait for all files to be present before stopping tether.
    while True:
        files = list(app_settings.image_dir.glob('*.cr2'))
        if len(files) == observation.num_exposures:
            break
        print(f'Waiting for files from camera: {len(files)} of {observation.num_exposures}')
        await sleep(0.5)

    await app_settings.camera.stop_tether()

    print(f'Finished observation in {(dt.utcnow() - obs_start_time).seconds}s')
    app_settings.is_observing = False
    return dict(success=True, message=f'Observation complete', files=files)


@app.post('/take-picture')
async def take_picture(exptime: float = 1.0):
    """Take a picture with the camera.

    This will not start the gphoto2 tether so will leave images on the camera.
    """
    await app_settings.camera.take_picture(exptime=exptime)
    return dict(success=True, message=f'Exposure complete')


@app.post('/download-recent')
async def download_recent(filename_pattern: str | None = None):
    """Download the most recent image from the camera."""
    files = await app_settings.camera.download_recent(filename_pattern)
    return dict(success=True, message=f'Download complete', files=files)


@app.post('/command')
async def gphoto2_command(command: GphotoCommand):
    """Run a gphoto2 command."""
    return await app_settings.camera.run_gphoto2_command(command)
