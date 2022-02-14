import re
import time
import shutil
import subprocess
from contextlib import suppress
from enum import IntEnum
from pathlib import Path
from typing import Optional, List, Dict, Union

import pigpio
from datetime import datetime as dt
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel, DirectoryPath, Field, BaseSettings


class State(IntEnum):
    LOW = 0
    HIGH = 1


class Settings(BaseSettings):
    gpio_pins: List[int] = [17, 18]


class Camera(BaseModel):
    name: str
    uid: str
    port: str
    pin: int


class AppSettings(BaseModel):
    base_dir: Optional[DirectoryPath] = Path('images')
    pins: List[int] = Field(default_factory=list)
    cameras: List[Camera] = Field(default_factory=list)
    ports: Dict = Field(default_factory=dict)
    processes: Dict = Field(default_factory=dict)
    is_observing: bool = False


class Observation(BaseModel):
    sequence_id: str
    exptime: Union[List[float], float]
    field_name: str = ''
    num_exposures: int = 1
    readout_time: float
    use_tether: bool = False


class GphotoCommand(BaseModel):
    """Accepts an arbitrary command string which is passed to gphoto2."""
    arguments: List[str] = '--auto-detect'
    port: Optional[str] = None,
    timeout: Optional[float] = 300
    return_property: bool = False


app_settings = AppSettings(pins=Settings().gpio_pins)
app = FastAPI()
gpio = pigpio.pi()


@app.on_event('startup')
async def startup_tasks():
    """Set up the cameras.

    If no settings are specified, this will attempt to associate a GPIO pin
    with a usb port via gphoto2.
    """
    # Get GPIO pins and set OUTPUT mode.
    for i, pin in enumerate(app_settings.pins):
        print(f'Setting {pin=} as OUTPUT')
        gpio.set_mode(pin, pigpio.OUTPUT)


@app.on_event('shutdown')
async def shutdown_tasks():
    print('Stopping any running gphoto2 tether processes')
    await stop_gphoto_tether()


@app.post('/take-observation')
async def take_observation(observation: Observation, background_tasks: BackgroundTasks):
    """Take a picture by setting GPIO port high"""
    if app_settings.is_observing:
        return dict(success=False, message=f'Observation already in progress')

    print(f'Taking picture for {observation.field_name=} with {observation.exptime=}')

    if observation.use_tether:
        await start_gphoto_tether(observation.sequence_id, observation.field_name)
        await sleep(1)

    pic_num = 1
    start_time = dt.utcnow()
    while pic_num <= observation.num_exposures:
        app_settings.is_observing = True
        print(f'Taking photo {pic_num:03d} of {observation.num_exposures:03d} '
              f'[{(dt.utcnow() - start_time).seconds}s]')

        for pin in app_settings.pins:
            background_tasks.add_task(release_shutter, pin, observation.exptime)

        print(f'Done with photo {pic_num:03d} of {observation.num_exposures:03d} '
              f'[{(dt.utcnow() - start_time).seconds}s]')

        await sleep(observation.readout_time)
        pic_num += 1

    if observation.use_tether:
        await stop_gphoto_tether()

    print(f'Done with observation [{(dt.utcnow() - start_time).seconds}s]')
    app_settings.is_observing = False
    return dict(success=True, message=f'Observation complete')


@app.get('/list-cameras')
async def list_connected_cameras(match_pins: bool = False) -> dict:
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

    camera_info = dict()
    for line in lines:
        camera_match = re.match(r'([\w\d\s_.]{30})\s(usb:\d{3},\d{3})', line)
        if camera_match:
            port = camera_match.group(2).strip()
            get_port_command = [gphoto2, '--port', port, '--get-config', 'serialnumber']
            completed_proc = subprocess.run(get_port_command, capture_output=True)
            cam_id = completed_proc.stdout.decode().split('\n')[3].split(' ')[-1][-6:]
            camera_info[cam_id] = port

    if match_pins:
        for i, (cam_id, port) in enumerate(camera_info.items()):
            cam_name = f'Cam{i:02d}'
            for pin in app_settings.pins:
                shutter_cmd = GphotoCommand(port=port,
                                            arguments=['--get-config', 'shuttercounter'],
                                            return_property=True)
                print(f'Checking pin for {cam_id=} on {port=}')
                before_count = await gphoto2_command(shutter_cmd)
                release_shutter(pin, 1)
                after_count = await gphoto2_command(shutter_cmd)
                print(f'Checking {after_count=} and {before_count=}')
                if int(after_count['output']) - int(before_count['output']) == 1:
                    camera = Camera(name=cam_name, port=port, pin=pin, uid=cam_id)
                    print(f'Loaded {camera=}')
                    app_settings.cameras.append(camera)
                    break

    return camera_info


@app.post('/gphoto')
async def gphoto2_command(command: GphotoCommand):
    """Perform a gphoto2 command."""
    full_command = await _build_gphoto2_command(command.arguments, command.port)
    print(f'Running gphoto2 {full_command=}')

    completed_proc = subprocess.run(full_command, capture_output=True, timeout=command.timeout)

    output = completed_proc.stdout.decode('utf-8').split('\n')
    if command.return_property:
        for line in output:
            print(f'Looking for property in {line=}')
            if line.startswith('Current: '):
                output = line.replace('Current: ', '')

    # Populate return items.
    command_output = dict(
        success=completed_proc.returncode >= 0,
        returncode=completed_proc.returncode,
        output=output,
        error=completed_proc.stderr.decode('utf-8').split('\n')
    )

    return command_output


@app.get('/tether')
async def tether_status():
    """Return status of gphoto2 tether."""

    def is_running(proc):
        if proc.poll() is None:
            return f'Running {proc.pid=}'
        else:
            return f'Stopped {proc.returncode=}'

    return {cam_id: is_running(p) for cam_id, p in app_settings.processes.items()}


async def _build_gphoto2_command(command: Union[List[str], str], port: Optional[str] = None):
    full_command = [shutil.which('gphoto2')]

    if port is not None:
        full_command.append('--port')
        full_command.append(port)

    # Turn command into a list if not one already.
    with suppress(AttributeError):
        command = command.split(' ')

    full_command.extend(command)

    return full_command


def release_shutter(pin: int, exptime: float):
    """Trigger the shutter release for given exposure time."""
    print(f'Triggering {pin=} for {exptime=} seconds at {dt.utcnow()}.')
    open_shutter(pin)
    time.sleep(exptime)
    close_shutter(pin)


def open_shutter(pin: int):
    """Opens the shutter for the camera."""
    gpio.write(pin, State.HIGH)


def close_shutter(pin: int):
    """Closes the shutter for the camera."""
    gpio.write(pin, State.LOW)


async def start_gphoto_tether(sequence_id, field_name):
    """Starts a gphoto2 tether and saves images to the given field_name."""
    gphoto2 = shutil.which('gphoto2')
    if not gphoto2:  # pragma: no cover
        raise Exception('gphoto2 is missing, please install or use the endpoint option.')

    cameras = await list_connected_cameras()
    for cam_id, port in cameras.items():
        output_dir = app_settings.base_dir / field_name
        filename_pattern = f'{output_dir}/{cam_id}/{sequence_id}/%Y%m%dT%H%M%S.cr2'
        print(f'Starting gphoto2 tether for {port=} using {filename_pattern=}')
        command = [gphoto2, '--port', port, '--filename', filename_pattern, '--capture-tethered']

        proc = subprocess.Popen(command)
        app_settings.processes[cam_id] = proc


async def stop_gphoto_tether():
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
