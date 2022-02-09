from celery import Celery
import re
import shutil
import subprocess
import time
from datetime import datetime as dt
from enum import IntEnum
from pathlib import Path
from typing import Optional, List, Dict, Union

import pigpio
from panoptes.utils.config.client import get_config
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
    is_observing: bool = False

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


@app.task(name='camera.take_observation')
def take_observation(sequence_id: str,
                     exptime: Union[List[float], float],
                     field_name: str = '',
                     num_exposures: int = 1,
                     use_tether: bool = False):
    """Take a picture by setting GPIO port high"""
    if app_settings.is_observing:
        return dict(success=False, message=f'Observation already in progress')

    print(f'Taking picture for {field_name=} with {exptime=}')

    if use_tether:
        start_gphoto_tether(sequence_id, field_name)
        time.sleep(1)

    pic_num = 1
    start_time = dt.utcnow()
    while True:
        app_settings.is_observing = True
        print(f'Taking photo {pic_num:03d} of {num_exposures:03d} '
              f'[{(dt.utcnow() - start_time).seconds}s]')

        cam_tasks = [app.send_task('camera.release_shutter', args=[pin, exptime])
                     for pin in app_settings.pins]

        # TODO(wtgee) change this.
        while all([t.status != 'SUCCESS' for t in cam_tasks]):
            print(f'Waiting for cameras to be finished with observation')
            time.sleep(1)

        print(f'Done with photo {pic_num:03d} of {num_exposures:03d} '
              f'[{(dt.utcnow() - start_time).seconds}s]')
        if pic_num == num_exposures:
            print(f'Reached {num_exposures=}, stopping photos')
            break
        else:
            pic_num += 1
            time.sleep(0.5)

    if use_tether:
        stop_gphoto_tether()

    print(f'Done with observation [{(dt.utcnow() - start_time).seconds}s]')
    app_settings.is_observing = False
    return dict(success=True, message=f'Observation complete')


@app.task(name='list-cameras')
def list_connected_cameras() -> dict:
    """Detect connected cameras.

    Uses gphoto2 to try and detect which cameras are connected. Cameras should
    be known and placed in config but this is a useful utility.

    Returns:
        dict: Camera names and usb ports from gphoto2.
    """
    gphoto2 = shutil.which('gphoto2')
    if not gphoto2:  # pragma: no cover
        raise Exception('gphoto2 is missing, please install or use the endpoint option.')
    command = [gphoto2, '--auto-detect']
    result = subprocess.check_output(command).decode('utf-8')
    lines = result.split('\n')

    cameras = dict()
    for line in lines:
        camera_match = re.match(r'([\w\d\s_.]{30})\s(usb:\d{3},\d{3})', line)
        if camera_match:
            port = camera_match.group(2).strip()
            get_port_command = [gphoto2, '--port', port, '--get-config', 'serialnumber']
            completed_proc = subprocess.run(get_port_command, capture_output=True)
            cam_id = completed_proc.stdout.decode().split('\n')[3].split(' ')[-1][-6:]
            cameras[cam_id] = port

    return cameras


@app.task(name='gphoto')
def gphoto(arguments: str = '--auto-detect'):
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
    completed_proc = subprocess.run(full_command, capture_output=True)

    # Populate return items.
    command_output = dict(
        success=completed_proc.returncode >= 0,
        returncode=completed_proc.returncode,
        output=completed_proc.stdout,
        error=completed_proc.stderr
    )

    print(f'Returning {command_output!r}')
    return command_output


@app.task('camera.release_shutter')
def release_shutter(pin: int, exptime: float):
    """Trigger the shutter release for given exposure time."""
    print(f'Triggering {pin=} for {exptime=} seconds at {dt.utcnow()}.')
    gpio.write(pin, State.HIGH)
    time.sleep(exptime)
    gpio.write(pin, State.LOW)


def start_gphoto_tether(sequence_id, field_name):
    """Starts a gphoto2 tether and saves images to the given field_name."""
    gphoto2 = shutil.which('gphoto2')
    if not gphoto2:  # pragma: no cover
        raise Exception('gphoto2 is missing, please install or use the endpoint option.')

    cameras = list_connected_cameras()
    for cam_id, port in cameras.items():
        output_dir = app_settings.base_dir / field_name
        filename_pattern = f'{output_dir}/{cam_id}/{sequence_id}/%Y%m%dT%H%M%S.%C'
        print(f'Starting gphoto2 tether for {port=} using {filename_pattern=}')
        command = [gphoto2, '--port', port, '--filename', filename_pattern, '--capture-tethered']

        proc = subprocess.Popen(command)
        app_settings.processes[cam_id] = proc


def stop_gphoto_tether():
    """Stops all gphoto tether processes."""
    for cam_id, proc in app_settings.processes.items():
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
