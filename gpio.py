import os
import re
import shutil
import subprocess
import time
from enum import IntEnum
from typing import Optional, List, Dict
from loguru import logger
import pigpio
import requests

from pydantic import BaseSettings, DirectoryPath, AnyHttpUrl
from fastapi import FastAPI
from panoptes.utils.time import current_time


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    base_dir: Optional[DirectoryPath]
    pins: Dict[int, str] = {17: '', 18: ''}
    cam_ids: Optional[List[str]]
    processes: Optional[List]


class CameraInfo(BaseSettings):
    cam_id: Optional[str]
    pin: Optional[int]
    port: Optional[str]


class Exposure(BaseSettings):
    cam_id: str
    exptime: float
    num_exposures: int = 1


settings = Settings(processes=list())
app = FastAPI()
gpio = pigpio.pi()


@app.on_event('startup')
def startup_tasks():
    # Get GPIO pins and set mode.
    for pin in settings.pins:
        print(f'Setting {pin=} as OUTPUT')
        gpio.set_mode(pin, pigpio.OUTPUT)


def start_gphoto_tether(sequence_id):
    gphoto2 = shutil.which('gphoto2')
    if not gphoto2:  # pragma: no cover
        raise Exception('gphoto2 is missing, please install or use the endpoint option.')

    home_dir = os.getenv('HOME')

    for port in list_connected_cameras():
        command = [gphoto2, '--port', port, '--get-config', 'serialnumber']
        completed_proc = subprocess.run(command, capture_output=True)
        cam_id = completed_proc.stdout.decode().split('\n')[3].split(' ')[-1][-6:]

        filename_pattern = f'${home_dir}/images/{cam_id}/{sequence_id}/%Y%m%dT%H%M%S.%C'
        print(f'Starting gphoto2 tether for {port=} using {filename_pattern=}')
        command = [gphoto2, '--port', port, '--filename', filename_pattern, '--capture-tethered']

        proc = subprocess.Popen(command)
        settings.processes.append(proc)


@app.post('/take-pics')
def take_pic(exposure: Exposure):
    """Take a picture by setting GPIO port high"""
    logger.info(f'Taking picture for {exposure.cam_id=} with {exposure.exptime=}')

    start_gphoto_tether(current_time(flatten=True))

    pin = 17

    pic_num = 1
    while True:
        print(f'Taking photo {pic_num} of {exposure.num_exposures}')
        gpio.write(pin, State.HIGH)
        time.sleep(exposure.exptime)
        gpio.write(pin, State.LOW)

        if pic_num == exposure.num_exposures:
            break
        else:
            pic_num += 1


@app.get('/list-cameras')
def list_connected_cameras(endpoint: Optional[AnyHttpUrl] = None):
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
