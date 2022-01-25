import time
from enum import IntEnum
from typing import Optional, List

import pigpio
from fastapi import FastAPI
from loguru import logger
from pydantic import BaseSettings, DirectoryPath


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    base_dir: Optional[DirectoryPath]
    pins: List[int] = [17, 18]
    cam_ids: Optional[List[str]]


class Exposure(BaseSettings):
    pin: int
    exptime: float
    num_exposures: int = 1


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
def take_pic(exposure: Exposure):
    """Take a picture by setting GPIO pin high"""
    logger.info(f'Taking picture for {exposure.pin=} with {exposure.exptime=}')

    pic_num = 1
    while True:
        print(f'Taking photo {pic_num} of {exposure.num_exposures}')
        gpio.write(exposure.pin, State.HIGH)
        time.sleep(exposure.exptime)
        gpio.write(exposure.pin, State.LOW)

        if pic_num == exposure.num_exposures:
            print(f'Reached {exposure.num_exposures=}, stopping')
            break
        else:
            pic_num += 1
