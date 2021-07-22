import shutil
import sys
import subprocess
from pathlib import Path
from typing import Optional, Dict, List
from threading import Lock
from datetime import datetime
from pydantic import BaseModel

from fastapi import FastAPI

from canon import shutter_index_lookup, iso_index_lookup, eosremoterelease_index_lookup
from panoptes.pocs.camera import list_connected_cameras
from panoptes.pocs.camera.gphoto.canon import Camera


class Exposure(BaseModel):
    exptime: float
    filename: Optional[str] = None
    base_dir: Path = '.'
    iso: int = 100


app = FastAPI()
cameras: List[Camera] = []
locks: Dict[str, Lock] = {}


@app.on_event('startup')
def startup_tasks():
    gphoto2_path = shutil.which('gphoto2')
    if gphoto2_path is None:
        print('Cannot find gphoto2, exiting system.')
        sys.exit(1)

    initialize_cameras()


def initialize_cameras():
    """Look for attached cameras"""
    for i, port in enumerate(list_connected_cameras()):
        cameras.append(Camera(port=port, name=f'Cam{i:02d}', db_type='memory'))

    print(f'Found {cameras=!r}')


@app.post('/camera/{device_number}/startexposure')
def take_pic(device_number: int, exposure: Exposure):
    """Takes a picture with the camera."""
    camera = cameras[device_number]
    port_lock = locks.get(camera.port, Lock())
    if port_lock.locked():
        return {'message': f'Another exposure is currently in process for {camera.port=}',
                'success': False}

    with port_lock:
        # Look up shutter index based on requested exptime, otherwise `0` for bulb.
        shutter_index = shutter_index_lookup.get(exposure.exptime, 0)

        # Look up iso index, otherwise `1` for 100.
        iso_index = iso_index_lookup.get(exposure.iso, 1)

        commands = [
            f'--port={camera.port}',
            '--set-config-index', f'iso={iso_index}',
            '--set-config-index', f'shutterspeed={shutter_index}',
            '--wait-event=1s',  # gphoto2 needs this.
        ]

        # If using `bulb` shutter index we need to specify wait time, otherwise just capture.
        if shutter_index == 0:
            # Manually build bulb command.
            bulb_start_index = eosremoterelease_index_lookup['Press Full']
            bulb_stop_index = eosremoterelease_index_lookup['Release Full']

            commands.extend([
                '--set-config-index', f'eosremoterelease={bulb_start_index}',
                f'--wait-event={exposure.exptime}s',
                '--set-config-index', f'eosremoterelease={bulb_stop_index}',
                '--wait-event-and-download=1s'
            ])
        else:
            commands.extend(['--capture-image-and-download'])

        # Set up filename in the base_dir.
        filename = exposure.filename or datetime.now().strftime('%Y%m%dT%H%M%S')
        full_path = f'{exposure.base_dir}/{filename}.cr2'
        commands.extend([f'--filename={full_path}'])

        # Build the full command.
        full_command = [shutil.which('gphoto2'), *commands]

        completed_proc = gphoto2_command(full_command)

        # Return the full path upon success otherwise the output from command.
        if completed_proc.returncode:
            print(completed_proc.stdout)
            return {'success': False, 'message': completed_proc.stdout}
        else:
            print(f'Done taking picture {completed_proc.returncode}')
            return {'success': True, 'filename': full_path}


@app.get('/camera/{device_number}/shutterspeeds')
def get_shutterspeeds(device_number: int):
    return shutter_index_lookup


def gphoto2_command(command: List[str]) -> subprocess.CompletedProcess:
    """Run a gphoto2 command in a separate blocking process.

    Return a subprocess.CompletedProcess.
    """
    print(f'Running {command=}')

    # Run the blocking command in a separate process.
    completed_proc = subprocess.run(command, capture_output=True)

    return completed_proc
