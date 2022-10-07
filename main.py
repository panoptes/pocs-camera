import shutil
import shutil
import subprocess
from contextlib import suppress
from datetime import datetime as dt
from enum import IntEnum
from pathlib import Path
from typing import Optional, List, Union

import pigpio
from anyio import sleep
from fastapi import FastAPI
from pydantic import BaseModel, DirectoryPath, BaseSettings


class State(IntEnum):
    LOW = 0
    HIGH = 1


class CameraSettings(BaseSettings):
    name: str
    uid: str
    port: str
    pin: int


class PinExposure(BaseModel):
    pin: int
    exptime: float


class Observation(BaseModel):
    sequence_id: str
    exptime: List[float] | float
    field_name: str = ''
    num_exposures: int = 1
    readout_time: float = 0.25
    filename_pattern: str = '%Y%m%dT%H%M%S.cr2'


class GphotoCommand(BaseModel):
    """Accepts an arbitrary command string which is passed to gphoto2."""
    arguments: List[str] | str = '--auto-detect'
    timeout: Optional[float] = 300
    return_property: bool = False


class AppSettings(BaseModel):
    camera: CameraSettings = CameraSettings()
    base_dir: DirectoryPath = Path('images')
    is_observing: bool = False
    observation: Observation | None = None
    process: subprocess.Popen | None = None

    @property
    def sequence_dir(self) -> Path:
        if self.observation is None:
            return self.base_dir

        output_dir = self.base_dir / self.observation.field_name
        sequence_dir = output_dir / self.camera.uid / self.observation.sequence_id

        return sequence_dir


app_settings = AppSettings()
app = FastAPI()
gpio = pigpio.pi()


@app.on_event('startup')
async def startup_tasks():
    """Set up the cameras.

    If no settings are specified, this will attempt to associate a GPIO pin
    with a usb port via gphoto2.
    """
    # Set GPIO pin to OUTPUT mode.
    pin = app_settings.camera.pin
    print(f'Setting {pin=} as OUTPUT')
    gpio.set_mode(pin, pigpio.OUTPUT)


@app.on_event('shutdown')
async def shutdown_tasks():
    print('Stopping any running gphoto2 tether processes')
    await stop_gphoto_tether()


@app.post('/take-observation')
async def take_observation(observation: Observation):
    """Take a sequence of exposures."""
    if app_settings.is_observing:
        return dict(success=False, message=f'Observation already in progress')
    else:
        app_settings.is_observing = True

    await start_gphoto_tether()

    obs_start_time = dt.utcnow()
    for pic_num in range(observation.num_exposures):
        # Take the image on each camera.
        print(f'Taking photo {pic_num:03d} of {observation.num_exposures:03d}')
        await release_shutter(exptime=observation.exptime)
        await sleep(observation.readout_time)
        print(f'Done with {pic_num=:03d} of {observation.num_exposures:03d}')

    # Wait for all files to be present before stopping tether.
    while True:
        file_list = list(app_settings.sequence_dir.glob('*.cr2'))
        if len(file_list) == observation.num_exposures:
            break
        print(f'Waiting for files from camera: {len(file_list)} of {observation.num_exposures}')
        await sleep(0.5)

    await stop_gphoto_tether()

    print(f'Finished observation in {(dt.utcnow() - obs_start_time).seconds}s')
    app_settings.is_observing = False
    return dict(success=True, message=f'Observation complete', files=file_list)


@app.post('/command')
async def gphoto2_command(command: GphotoCommand):
    """Perform a gphoto2 command."""
    full_command = await _build_gphoto2_command(command.arguments)
    print(f'Running gphoto2 {full_command=}')

    completed_proc = subprocess.run(full_command, capture_output=True, timeout=command.timeout)

    output = completed_proc.stdout.decode('utf-8').split('\n')
    if command.return_property:
        for line in output:
            if line.startswith('Current: '):
                output = line.replace('Current: ', '')
                print(f'Found property: {output}')
                break

    # Populate return items.
    command_output = dict(
        success=completed_proc.returncode >= 0,
        returncode=completed_proc.returncode,
        output=output,
        error=completed_proc.stderr.decode('utf-8').split('\n')
    )

    return command_output


@app.post('/release-shutter')
async def release_shutter(exptime: float = 1.0):
    """Trigger the shutter release for given exposure time."""
    print(f'Exposing for {exptime=} seconds at {dt.utcnow()}.')
    await open_shutter()
    await sleep(exptime)
    await close_shutter()

    return dict(success=True, message=f'Exposure complete')


async def open_shutter():
    """Opens the shutter for the camera."""
    gpio.write(app_settings.camera.pin, State.HIGH)


async def close_shutter():
    """Closes the shutter for the camera."""
    gpio.write(app_settings.camera.pin, State.LOW)


async def start_gphoto_tether():
    """Starts a gphoto2 tether and saves images to the given directory."""
    sequence_dir = app_settings.sequence_dir
    filename_pattern = app_settings.observation.filename_pattern
    filename = f'{sequence_dir.as_posix()}/{filename_pattern}'

    full_command = await _build_gphoto2_command(['--filename', filename, '--capture-tethered'])

    print(f'Starting gphoto2 tether for {app_settings.camera.name} using {filename=}')
    app_settings.process = subprocess.Popen(full_command)

    # The cameras need a second to connect.
    await sleep(1)


async def stop_gphoto_tether():
    """Stop gphoto tether process."""
    proc = app_settings.process

    if proc is not None:
        outs = errs = ''
        try:
            outs, errs = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            outs, errs = proc.communicate()
        finally:
            if outs and outs > '':
                print(f'{outs=}')
            if errs and errs > '':
                print(f'{errs=}')


async def _build_gphoto2_command(command: List[str] | str) -> List[str]:
    full_command = [shutil.which('gphoto2'), '--port', app_settings.camera.port]

    # Turn command into a list if not one already.
    with suppress(AttributeError):
        command = command.split(' ')

    full_command.extend(command)

    return full_command
