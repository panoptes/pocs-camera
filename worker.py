import re
import shutil
import subprocess
import typing
from contextlib import suppress
from enum import IntEnum
from typing import Optional, List, Dict, Union

import pigpio
from celery import Celery
from panoptes.utils.time import current_time, CountdownTimer
from pydantic import BaseModel, Field, BaseSettings


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    camera_name: str
    camera_port: str
    camera_pin: int
    broker_url: str = 'amqp://guest:guest@localhost:5672//'
    result_backend: str = 'rpc://'

    class Config:
        env_prefix = 'pocs_'


class Camera(BaseModel):
    """A camera with a shutter release connected to a gpio pin."""
    name: str
    port: str
    pin: int
    is_tethered: bool = False

    def setup_pin(self):
        """Sets the mode for the GPIO pin."""
        # Get GPIO pin and set OUTPUT mode.
        print(f'Setting {self.pin=} as OUTPUT for {self.name}')
        gpio.set_mode(self.pin, pigpio.OUTPUT)


class AppSettings(BaseModel):
    celery: Dict = Field(default_factory=dict)
    camera: Camera
    process: Optional[typing.Any] = None


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

# Start celery.
app = Celery()
app.config_from_object(app_settings.celery)

# Setup GPIO pins.
gpio = pigpio.pi()
app_settings.camera.setup_pin()

camera_match_re = re.compile(r'([\w\d\s_.]{30})\s(usb:\d{3},\d{3})')
file_save_re = re.compile(r'Saving file as (.*)')


@app.task(name='camera.release_shutter', bind=True)
def release_shutter(self, exptime: float):
    """Trigger the shutter release for given exposure time via the GPIO pin."""
    start_time = current_time(flatten=True)
    self.update_state(state='EXPOSING', start_time=start_time, secs=0, exptime=exptime)

    # Create a timer.
    timer = CountdownTimer(exptime, name=f'Pin{app_settings.camera.pin}Expose')

    # Open shutter.
    gpio.write(app_settings.camera.pin, State.HIGH)

    while timer.expired() is False:
        self.update_state(state='EXPOSING',
                          meta=dict(
                              start_time=start_time,
                              secs=f'{exptime - timer.time_left():.02f}',
                              exptime=exptime))

        timer.sleep(max_sleep=max(1., exptime / 8))

    # Close shutter.
    gpio.write(app_settings.camera.pin, State.LOW)

    return start_time


@app.task(name='camera.start_tether', bind=True)
def start_gphoto2_tether(self, filename_pattern: str):
    """Start a tether for gphoto2 auto-download."""
    if app_settings.camera.is_tethered:
        print(f'{app_settings.camera} is already tethered')
        return
    else:
        print(f'Starting gphoto2 tether for {app_settings.camera.port=} using {filename_pattern=}')
        app_settings.camera.is_tethered = True

    command = ['--filename', filename_pattern, '--capture-tethered']
    full_command = _build_gphoto2_command(command)

    # Start tether process.
    app_settings.process = subprocess.Popen(full_command,
                                            stderr=subprocess.STDOUT,
                                            stdout=subprocess.PIPE)
    print(f'gphoto2 tether started for {app_settings.camera} on {app_settings.process.pid=}')


@app.task(name='camera.stop_tether')
def stop_gphoto2_tether():
    """Tells camera to stop gphoto2 tether."""
    print(f'Stopping gphoto2 tether for {app_settings.camera}')
    # Communicate and kill immediately.
    try:
        outs, errs = app_settings.process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        app_settings.process.kill()
        outs, errs = app_settings.process.communicate()

    app_settings.camera.is_tethered = False

    return dict(outs=outs.decode('utf-8'), errs=errs.decode('utf-8'))


@app.task(name='camera.file_download', bind=True)
def gphoto_file_download(self,
                         filename_pattern: str,
                         only_new: bool = True
                         ):
    """Downloads (newer) files from the camera on the given port using the filename pattern."""
    print(f'Starting gphoto2 download for {app_settings.camera} using {filename_pattern=}')
    command = ['--filename', filename_pattern, '--get-all-files', '--recurse']
    if only_new:
        command.append('--new')

    results = gphoto2_command(command, timeout=600)
    filenames = list()
    for line in results['output']:
        file_match = file_save_re.match(line)
        if file_match is not None:
            fn = file_match.group(1).strip()
            print(f'Found match {fn}')
            filenames.append(fn)
            self.update_state(state='DOWNLOADING', meta=dict(directory=fn))

    return filenames


@app.task(name='camera.delete_files', bind=True)
def gphoto_file_delete(self):
    """Removes all files from the camera on the given port."""
    print(f'Deleting all files for {app_settings.camera}')
    gphoto2_command('--delete-all-files --recurse')


@app.task(name='camera.command', bind=True)
def gphoto_task(self, command: Union[List[str], str]):
    """Perform arbitrary gphoto2 command.."""
    print(f'Calling {command=} on {app_settings.camera}')
    return gphoto2_command(command)


def gphoto2_command(command: Union[List[str], str], timeout: Optional[float] = 300) -> dict:
    """Perform a gphoto2 command."""
    full_command = _build_gphoto2_command(command)
    print(f'Running gphoto2 {full_command=}')

    completed_proc = subprocess.run(full_command, capture_output=True, timeout=timeout)

    # Populate return items.
    command_output = dict(
        success=completed_proc.returncode >= 0,
        returncode=completed_proc.returncode,
        output=completed_proc.stdout.decode('utf-8').split('\n'),
        error=completed_proc.stderr.decode('utf-8').split('\n')
    )

    return command_output


def _build_gphoto2_command(command: Union[List[str], str]):
    full_command = [shutil.which('gphoto2'), '--port', app_settings.camera.port]

    # Turn command into a list if not one already.
    with suppress(AttributeError):
        command = command.split(' ')

    full_command.extend(command)

    return full_command
