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
from panoptes.utils.config.client import get_config
from panoptes.utils.utils import listify
from pydantic import BaseModel, DirectoryPath, Field, BaseSettings


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    gpio_pins: List[int] = [17, 18]


class AppSettings(BaseModel):
    base_dir: Optional[DirectoryPath] = Path('images')
    pins: List[int] = Field(default_factory=list)
    celery: Dict = Field(default_factory=dict),
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
app_settings = AppSettings(pins=Settings().gpio_pins, celery=get_config('celery', default=dict()))

# Start celery.
app = Celery()
app.config_from_object(app_settings.celery)

# Setup GPIO pins.
gpio = pigpio.pi()
app_settings.setup_pins()


def release_shutter(pins: Union[List[int], int], exptimes: Union[List[float], float]):
    """Trigger the shutter release for given exposure time via the GPIO pins."""
    for exptime in listify(exptimes):
        print(f'Triggering {pins=} for {exptime=} seconds.')

        for pin in pins:
            gpio.write(pin, State.HIGH)

        print(f'Sleeping for {exptime=} seconds.')
        time.sleep(exptime)

        for pin in pins:
            gpio.write(pin, State.LOW)

        print(f'Done on {pins=} after {exptime=} seconds.')


@app.task(name='camera.take_observation', bind=True)
def take_observation(self,
                     exptime: Union[List[float], float],
                     field_name: str = '',
                     num_exposures: int = 1):
    """Take a sequence of images via GPIO shutter trigger."""
    print(f'Taking picture for {field_name=} with {exptime=}')

    pic_num = 1
    while pic_num <= num_exposures:
        self.update_state(state='OBSERVING',
                          meta=dict(current=pic_num, num_exposures=num_exposures))
        release_shutter(app_settings.pins, exptime)

        time.sleep(0.1)  # Small pause
        pic_num += 1


@app.task(name='camera.list', bind=True)
def list_connected_cameras(self) -> dict:
    """Detect connected cameras.

    Uses gphoto2 to try and detect which cameras are connected. Cameras should
    be known and placed in config but this is a useful utility.

    Returns:
        dict: Camera names and usb ports from gphoto2.
    """
    gphoto2 = shutil.which('gphoto2')
    if not gphoto2:  # pragma: no cover
        raise Exception('gphoto2 is missing, please install.')
    command = [gphoto2, '--auto-detect']
    lines = app.send_task('gphoto2.command', args=[command]).get().split('\n')

    cameras = dict()
    for line in lines:
        camera_match = re.match(r'([\w\d\s_.]{30})\s(usb:\d{3},\d{3})', line)
        if camera_match:
            port = camera_match.group(2).strip()
            get_serial_cmd = [gphoto2, '--port', port, '--get-config', 'serialnumber']
            result = app.send_task('gphoto2.command', args=[get_serial_cmd]).get().split('\n')
            cam_id = result[3].split(' ')[-1][-6:]
            cameras[cam_id] = port

    return cameras


@app.task(name='camera.command')
def gphoto_command(arguments: str = '--auto-detect', timeout: float = 300):
    """Perform arbitrary gphoto2 """
    print(f'Received gphoto2 command request')

    # Fix the filename.
    filename_match = re.search(r'--filename (.*.cr2)', arguments)
    if filename_match:
        filename_path = Path(filename_match.group(1))

        # If the application has a base directory, save there with same filename.
        if app_settings.base_dir is not None:
            app_filename = app_settings.base_dir / filename_path
            filename_in_args = f'--filename {str(filename_path)}'
            print(f'Replacing {filename_path} with {app_filename}.')
            arguments = arguments.replace(filename_in_args,
                                          f'--filename {app_filename}')

    # Build the full
    full_command = [shutil.which('gphoto2'), *arguments.split(' ')]

    print(f'Running {full_command!r}')
    task = app.send_task('gphoto2.command', args=[full_command])
    command_output = task.get(timeout=timeout)

    print(f'Returning {command_output!r}')
    return command_output


@app.task(name='camera.file_download', bind=True)
def gphoto_file_download(self,
                         port: str,
                         filename_pattern: str,
                         only_new: bool = True,
                         timeout: float = 300):
    """Downloads (newer) files from the camera on the given port using the filename pattern."""
    print(f'Starting gphoto2 tether for {port=} using {filename_pattern=}')
    command = [
        shutil.which('gphoto2'),
        '--port', port,
        '--filename', filename_pattern,
        '--get-all-files',
        '--recurse'
    ]
    if only_new:
        command.append('--new')

    app.send_task('gphoto2.command', args=[command], ignore_result=True)


@app.task(name='camera.delete_files', bind=True)
def gphoto_file_delete(self, port: str):
    """Removes all files from the camera on the given port."""
    print(f'Deleting all files for {port=}')
    command = [
        shutil.which('gphoto2'),
        '--port', port,
        '--delete-all-files',
        '--recurse'
    ]
    app.send_task('gphoto2.command', args=[command], ignore_result=True)


def lock_gphoto2(callback, *decorator_args, **decorator_kwargs):
    """ Decorator to ensure only one instance of the task is running at once. """

    @wraps(callback)
    def _wrapper(task, *args, **kwargs):
        if task.name.startswith('gphoto2.'):
            for queue in task.app.control.inspect().active():
                for running_task in queue:
                    if running_task['name'].startswith('gphoto2.'):
                        if task.request.id != running_task['id']:
                            return f'Another gphoto2 task is already in progress'

        return callback(task, *args, **kwargs)

    return _wrapper


@app.task(name='gphoto2.command', bind=True)
@lock_gphoto2
def gphoto2_command(self, command: str, timeout: float = 300):
    """Perform a gphoto2 command."""
    completed_proc = subprocess.run(command, capture_output=True, timeout=timeout)

    # Populate return items.
    command_output = dict(
        success=completed_proc.returncode >= 0,
        returncode=completed_proc.returncode,
        output=completed_proc.stdout,
        error=completed_proc.stderr
    )

    return command_output
