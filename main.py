import os
import re
import shutil
import subprocess
import time
from enum import IntEnum
from pathlib import Path
from typing import Optional, List, Dict, Union

from anyio import sleep
from loguru import logger
import pigpio
import requests

from pydantic import BaseModel, DirectoryPath, AnyHttpUrl, Field, BaseSettings
from fastapi import FastAPI


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    gpio_pins: List[int] = [17, 18]


class AppSettings(BaseModel):
    base_dir: Optional[DirectoryPath]
    pins: List[int] = Field(default_factory=list)
    cameras: Dict = Field(default_factory=dict)
    processes: Dict = Field(default_factory=dict)


class Observation(BaseModel):
    output_directory: str
    exptime: Union[List[float], float]
    num_exposures: int = 1


class GphotoCommand(BaseModel):
    """Accepts an arbitrary command string which is passed to gphoto2."""
    arguments: str = '--auto-detect'
    success: bool = False
    output: Optional[str]
    error: Optional[str]
    returncode: Optional[int]


app_settings = AppSettings(pins=Settings().gpio_pins)
app = FastAPI()
gpio = pigpio.pi()


@app.on_event('startup')
def startup_tasks():
    """Set up the cameras.

    If no settings are specified, this will attempt to associate a GPIO pin
    with a usb port via gphoto2.
    """
    # Get GPIO pins and set OUTPUT mode.
    for i, pin in enumerate(app_settings.pins):
        cam_name = f'Cam{i:02d}'
        print(f'Setting {pin=} as OUTPUT and assigning {cam_name=}')
        gpio.set_mode(pin, pigpio.OUTPUT)
        app_settings.cameras[cam_name] = pin


@app.on_event('shutdown')
def shutdown_tasks():
    print('Stopping any running gphoto2 tether processes')
    await stop_gphoto_tether()


@app.post('/take-observation')
async def take_observation(observation: Observation):
    """Take a picture by setting GPIO port high"""
    logger.info(f'Taking picture for {observation.output_directory=} with {observation.exptime=}')

    await start_gphoto_tether(observation.output_directory)
    time.sleep(2)

    pic_num = 1
    while True:
        for pin in app_settings.pins:
            print(f'Taking photo {pic_num:03d} of {observation.num_exposures:03d}')
            await release_shutter(pin, exptime=observation.exptime)

        if pic_num == observation.num_exposures:
            print(f'Reached {observation.num_exposures=}, stopping photos')
            break
        else:
            pic_num += 1
            time.sleep(0.5)

    await stop_gphoto_tether()


@app.get('/list-cameras')
async def list_connected_cameras(endpoint: Optional[AnyHttpUrl] = None):
    """Detect connected cameras.

    Uses gphoto2 to try and detect which cameras are connected. Cameras should
    be known and placed in config but this is a useful utility.

    Returns:
        list: A list of the ports with detected cameras.
    """

    result = ''
    if endpoint is not None:
        response = requests.post(endpoint, json=dict(arguments='--auto-detect'))
        if response.ok:
            result = response.json()['output']
    else:
        gphoto2 = shutil.which('gphoto2')
        if not gphoto2:  # pragma: no cover
            raise Exception('gphoto2 is missing, please install or use the endpoint option.')
        command = [gphoto2, '--auto-detect']
        result = subprocess.check_output(command).decode('utf-8')
    lines = result.split('\n')

    ports = []

    for line in lines:
        camera_match = re.match(r'([\w\d\s_.]{30})\s(usb:\d{3},\d{3})', line)
        if camera_match:
            # camera_name = camera_match.group(1).strip()
            port = camera_match.group(2).strip()
            ports.append(port)

    return ports


@app.post('/{cam_name}/gphoto')
async def gphoto(cam_name: str, command: GphotoCommand):
    """Perform arbitrary gphoto2 command."""
    logger.info(f'Received command={command!r}')

    # Fix the filename.
    filename_match = re.search(r'--filename (.*.cr2)', command.arguments)
    if filename_match:
        filename_path = Path(filename_match.group(1))

        # If the application has a base directory, save there with same filename.
        if app_settings.base_dir is not None:
            app_filename = app_settings.base_dir / filename_path
            filename_in_args = f'--filename {str(filename_path)}'
            logger.debug(f'Replacing {filename_path} with {app_filename}.')
            command.arguments = command.arguments.replace(filename_in_args,
                                                          f'--filename {app_filename}')

    # Build the full command.
    full_command = [shutil.which('gphoto2'), *command.arguments.split(' ')]

    logger.debug(f'Running {full_command!r}')
    completed_proc = subprocess.run(full_command, capture_output=True)

    # Populate return items.
    command.success = completed_proc.returncode >= 0
    command.returncode = completed_proc.returncode
    command.output = completed_proc.stdout
    command.error = completed_proc.stderr

    logger.info(f'Returning {command!r}')
    return command


async def release_shutter(pin: int, exptime: float):
    """Trigger the shutter release for given exposure time."""
    print(f'Triggering {pin=} for {exptime=} seconds.')
    await open_shutter(pin)
    await sleep(exptime)
    await close_shutter(pin)


async def open_shutter(pin: int):
    """Opens the shutter for the camera."""
    gpio.write(pin, State.HIGH)


async def close_shutter(pin: int):
    """Closes the shutter for the camera."""
    gpio.write(pin, State.LOW)


async def start_gphoto_tether(output_directory):
    """Starts a gphoto2 tether and saves images to the given output_directory."""
    gphoto2 = shutil.which('gphoto2')
    if not gphoto2:  # pragma: no cover
        raise Exception('gphoto2 is missing, please install or use the endpoint option.')

    home_dir = os.getenv('HOME')

    for port in list_connected_cameras():
        command = [gphoto2, '--port', port, '--get-config', 'serialnumber']
        completed_proc = subprocess.run(command, capture_output=True)
        cam_id = completed_proc.stdout.decode().split('\n')[3].split(' ')[-1][-6:]

        filename_pattern = f'{home_dir}/images/{cam_id}/{output_directory}/%Y%m%dT%H%M%S.%C'
        print(f'Starting gphoto2 tether for {port=} using {filename_pattern=}')
        command = [gphoto2, '--port', port, '--filename', filename_pattern, '--capture-tethered']

        proc = subprocess.Popen(command)
        app_settings.processes.append(proc)


async def stop_gphoto_tether():
    """Stops all gphoto tether processes."""
    for proc in app_settings.processes:
        try:
            outs, errs = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            outs, errs = proc.communicate()
        finally:
            if outs > '':
                print(f'{outs=}')
            if errs > '':
                print(f'{errs=}')
