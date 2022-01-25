import re
import shutil
import subprocess
import time
from enum import Enum
from typing import Optional, List
from loguru import logger
import pigpio
import requests

from pydantic import BaseSettings, DirectoryPath, AnyHttpUrl
from fastapi import FastAPI


class State(Enum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    base_dir: Optional[DirectoryPath]
    pins: List[int] = [17, 18]
    cam_ids: Optional[List[str]]


settings = Settings()
app = FastAPI()
gpio = pigpio.pi()


@app.on_event('startup')
def startup_tasks():
    # Get GPIO pins and set mode.
    for pin in settings.pins:
        print(f'Setting {pin=} as OUTPUT')
        gpio.set_mode(pin, pigpio.OUTPUT)


@app.post('/take-pic')
def take_pic(pin: int, exptime: float, num_exposures=1):
    """Take a picture by setting GPIO pin high"""
    logger.info(f'Taking picture for {pin=} with {exptime=}')

    pic_num = 1
    while True:
        print(f'Taking photo {pic_num} of {num_exposures}')
        gpio.write(pin, State.HIGH)
        time.sleep(exptime)
        gpio.write(pin, State.LOW)

        if pic_num == num_exposures:
            print(f'Reached {num_exposures=}, stopping')
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
