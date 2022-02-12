from contextlib import suppress
from functools import wraps

from celery import Celery
import re
import shutil
import subprocess
import time
from enum import IntEnum
from pathlib import Path
from typing import Optional, List, Dict, Union

import pigpio
from panoptes.utils.utils import listify
from pydantic import BaseModel, DirectoryPath, Field, BaseSettings, AmqpDsn


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    gpio_pins: List[int] = [17, 18]
    broker_url: AmqpDsn = 'amqp://guest:guest@localhost:5672//'
    result_backend: str = 'rpc://'

    class Config:
        env_file = '.env'
        env_prefix = 'pocs_'


class AppSettings(BaseModel):
    base_dir: Optional[DirectoryPath] = Path('images')
    pins: List[int] = Field(default_factory=list)
    celery: Dict = Field(default_factory=dict)
    cameras: Dict = Field(default_factory=dict)
    processes: Dict = Field(default_factory=dict)

    def setup_pins(self):
        """Sets the mode for the GPIO pins"""
        # Get GPIO pins and set OUTPUT mode.
        for i, pin in enumerate(self.pins):
            cam_name = f'Cam{i:02d}'
            print(f'Setting {pin=} as OUTPUT and assigning {cam_name=}')
            gpio.set_mode(pin, pigpio.OUTPUT)
            self.cameras[cam_name] = pin


# Get overall settings.
settings = Settings()
app_settings = AppSettings(pins=settings.gpio_pins, celery=dict(broker_url=settings.broker_url,
                                                                result_backend=settings.result_backend))

# Start celery.
app = Celery()
app.config_from_object(app_settings.celery)

# Setup GPIO pins.
gpio = pigpio.pi()
app_settings.setup_pins()

camera_match_re = re.compile(r'([\w\d\s_.]{30})\s(usb:\d{3},\d{3})')
file_save_re = re.compile(r'Saving file as (.*)')


def lock_gphoto2(callback, *decorator_args, **decorator_kwargs):
    """ Decorator to ensure only one instance of the task is running at once. """

    @wraps(callback)
    def _wrapper(task, *args, **kwargs):
        if task.name.startswith('gphoto2.'):
            for queue in task.app.control.inspect().active():
                for running_task in queue:
                    if running_task['name'].startswith('gphoto2.'):
                        with suppress(IndexError):
                            print(f"port: running_task['kwargs']['port'] == task.kwargs['port']")
                            print(f"id: running_task['id'] == task.request.id")
                            same_port = running_task['kwargs']['port'] == task.kwargs['port']
                            different_tasks = task.request.id != running_task['id']
                            if same_port and different_tasks:
                                print(f'Another gphoto2 task is already in progress')
                                return

        return callback(task, *args, **kwargs)

    return _wrapper


def release_shutter(pins: Union[List[int], int], exptimes: Union[List[float], float]):
    """Trigger the shutter release for given exposure time via the GPIO pins."""
    for exptime in listify(exptimes):

        for pin in pins:
            gpio.write(pin, State.HIGH)

        print(f'Shutter open for {exptime=} seconds on {pins=}.')
        time.sleep(exptime)

        for pin in pins:
            gpio.write(pin, State.LOW)


@app.task(name='camera.take_observation', bind=True)
def take_observation(self, exptime: Union[List[float], float], num_exposures: int = 1,
                     readout_time: float = 0.25):
    """Take a sequence of images via GPIO shutter trigger."""
    pic_num = 1
    while pic_num <= num_exposures:
        self.update_state(state='OBSERVING',
                          meta=dict(current=pic_num, num_exposures=num_exposures))
        print(f'Image {pic_num:03d}/{num_exposures:03d} for {exptime=}s.')
        release_shutter(app_settings.pins, exptime)
        print(f'Done with {pic_num}/{num_exposures} after {exptime=}s.')

        time.sleep(readout_time)
        pic_num += 1


@app.task(name='gphoto2.list', bind=True)
def list_connected_cameras(self) -> dict:
    """Detect connected cameras.

    Uses gphoto2 to try and detect which cameras are connected. Cameras should
    be known and placed in config but this is a useful utility.

    Returns:
        dict: Camera names and usb ports from gphoto2.
    """
    result = gphoto2_command('--auto-detect')

    cameras = dict()
    for line in result['output']:
        camera_match = camera_match_re.match(line)
        if camera_match:
            port = camera_match.group(2).strip()
            result = gphoto2_command('--get-config serialnumber', port=port)
            cam_id = result['output'][3].split(' ')[-1][-6:]
            cameras[cam_id] = port

    return cameras


@app.task(name='gphoto2.file_download', bind=True)
def gphoto_file_download(self,
                         filename_pattern: str,
                         port: Optional[str] = None,
                         only_new: bool = True
                         ):
    """Downloads (newer) files from the camera on the given port using the filename pattern."""
    print(f'Starting gphoto2 download for {port=} using {filename_pattern=}')
    command = ['--filename', filename_pattern, '--get-all-files', '--recurse']
    if only_new:
        command.append('--new')

    results = gphoto2_command(command, port=port, timeout=600)
    filenames = list()
    for line in results['output']:
        file_match = file_save_re.match(line)
        if file_match is not None:
            print(f'Found match {file_match.group(1)}')
            filenames.append(file_match.group(1).strip())

    return filenames


@app.task(name='gphoto2.tether', bind=True)
def gphoto_tether(self,
                  filename_pattern: str,
                  port: Optional[str] = None,
                  ):
    """Start a tether for gphoto2 auto-download."""
    print(f'Starting gphoto2 tether for {port=} using {filename_pattern=}')
    gphoto2_command(['--filename', filename_pattern, '--capture-tether'], port=port)


@app.task(name='gphoto2.delete_files', bind=True)
def gphoto_file_delete(self, port: Optional[str] = None):
    """Removes all files from the camera on the given port."""
    print(f'Deleting all files for {port=}')
    gphoto2_command('--delete-all-files --recurse', port=port)


@app.task(name='gphoto2.command', bind=True)
def gphoto_command(self, command: Union[List[str], str], port: Optional[str] = None):
    """Perform arbitrary gphoto2 command.."""
    print(f'Calling {command=} on {port=}')
    return gphoto2_command(command, port=port)


def gphoto2_command(command: Union[List[str], str], port: Optional[str] = None,
                    timeout: float = 300) -> dict:
    """Perform a gphoto2 command."""
    full_command = _build_gphoto2_command(command, port)
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


def _build_gphoto2_command(command: Union[List[str], str], port: Optional[str] = None):
    full_command = [shutil.which('gphoto2')]

    if port is not None:
        full_command.append('--port')
        full_command.append(port)

    # Turn command into a list if not one already.
    with suppress(AttributeError):
        command = command.split(' ')

    full_command.extend(command)

    return full_command
