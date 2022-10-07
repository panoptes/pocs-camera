import asyncio
import shutil
import subprocess
from asyncio import sleep
from contextlib import suppress
from enum import IntEnum
from pathlib import Path
from typing import List, Optional
from datetime import datetime as dt

import pigpio
from pydantic import BaseSettings, BaseModel

from panoptes.utils.time import CountdownTimer


class ShutterState(IntEnum):
    CLOSED = 0
    OPEN = 1


class CameraSettings(BaseSettings):
    name: str
    uid: str
    port: str
    pin: int
    filename_pattern: str = '%Y%m%dT%H%M%S.cr2'


class GphotoCommand(BaseModel):
    """Accepts an arbitrary command string which is passed to gphoto2."""
    arguments: List[str] | str = '--auto-detect'
    timeout: Optional[float] = 300
    return_property: bool = False


class Camera:
    """A simple camera class.

    This class uses gphoto2 to change settings on the camera and to start a
    tether. The camera is triggered by a GPIO pin.
    """

    def __init__(self, camera_settings: CameraSettings | None = None):
        self.gpio = pigpio.pi()
        self.camera_settings = camera_settings or CameraSettings()

        self.tether_process: subprocess.Popen | None = None
        self.exposure_timer: CountdownTimer | None = None

        # Set GPIO pin to OUTPUT mode.
        print(f'Setting {self.camera_settings.pin=} as OUTPUT')
        self.gpio.set_mode(self.camera_settings.pin, pigpio.OUTPUT)

    @property
    def is_exposing(self) -> bool:
        return self.exposure_timer is not None and not self.exposure_timer.expired()

    @property
    def is_tethered(self) -> bool:
        return self.tether_process is not None and self.tether_process.poll() is None

    @property
    def shutter_state(self):
        return ShutterState(self.gpio.read(self.camera_settings.pin))

    async def take_picture(self, exptime: float = 1.0):
        """Takes a picture with the camera."""
        print(f'Exposing for {exptime=} seconds at {dt.utcnow()}.')
        self.exposure_timer = CountdownTimer(exptime)
        await self.open_shutter()
        await asyncio.sleep(exptime)
        await self.close_shutter()
        self.exposure_timer = None
        print(f'Finished exposing at {dt.utcnow()}.')

    async def open_shutter(self):
        """Opens the shutter."""
        self.gpio.write(self.camera_settings.pin, ShutterState.OPEN)

    async def close_shutter(self):
        """Closes the shutter."""
        self.gpio.write(self.camera_settings.pin, ShutterState.CLOSED)

    async def download_recent(self, filename_pattern: str | None = None) -> List[Path]:
        """Download the most recent image from the camera."""
        filename_pattern = filename_pattern or self.camera_settings.filename_pattern
        print(f'Downloading recent image for {self.camera_settings.name} with {filename_pattern=}')
        command = GphotoCommand(arguments=['--get-all-files',
                                           '--new',
                                           '--filename', filename_pattern
                                           ])
        command_output = await self.run_gphoto2_command(command)

        files = list()
        if command_output['success']:
            for line in command_output['output']:
                if line.startswith('Saving file as '):
                    filename = line.replace('Saving file as ', '')
                    print(f'Found recent image: {filename}')
                    files.append(Path(filename))

        return files

    async def run_gphoto2_command(self, command: GphotoCommand) -> dict:
        """Perform a gphoto2 command."""
        full_command = self._build_gphoto2_command(command.arguments)
        print(f'Running gphoto2 {full_command=}')

        completed_proc = subprocess.run(full_command, capture_output=True, timeout=command.timeout)

        output = completed_proc.stdout.decode('utf-8').split('\n')
        if command.return_property:
            for line in output:
                if line.startswith('Current: '):
                    output = line.replace('Current: ', '')
                    print(f'Found property: {output}')
                    break

        # Populate return items.
        command_output = dict(
            success=completed_proc.returncode >= 0,
            returncode=completed_proc.returncode,
            output=output,
            error=completed_proc.stderr.decode('utf-8').split('\n')
        )

        return command_output

    async def start_gphoto_tether(self, sequence_dir: Path = Path('.'),
                                  filename_pattern: str | None = None):
        """Starts a gphoto2 tether and saves images to the given directory."""
        filename_pattern = filename_pattern or self.camera_settings.filename_pattern
        filename = f'{sequence_dir.as_posix()}/{filename_pattern}'

        full_command = self._build_gphoto2_command(['--filename', filename, '--capture-tethered'])

        print(f'Starting gphoto2 tether for {self.camera_settings.name} using {filename=}')
        self.tether_process = subprocess.Popen(full_command)

        # The cameras need a second to connect.
        await sleep(1)

    async def stop_gphoto_tether(self):
        """Stop gphoto tether process."""
        print(f'Stopping gphoto2 tether for {self.camera_settings.name}')
        if self.tether_process is not None:
            outs = errs = ''
            try:
                outs, errs = self.tether_process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                self.tether_process.kill()
                outs, errs = self.tether_process.communicate()
            finally:
                if outs and outs > '':
                    print(f'{outs=}')
                if errs and errs > '':
                    print(f'{errs=}')

                self.tether_process = None

    def _build_gphoto2_command(self, command: List[str] | str) -> List[str]:
        full_command = [shutil.which('gphoto2'), '--port', self.camera_settings.port]

        # Turn command into a list if not one already.
        with suppress(AttributeError):
            command = command.split(' ')

        full_command.extend(command)

        return full_command

    def __del__(self):
        self.gpio.stop()
