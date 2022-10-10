import shutil
import subprocess
from contextlib import suppress
from enum import IntEnum
from pathlib import Path
from typing import List, Optional
from datetime import datetime as dt
from time import sleep

from pydantic import BaseSettings, BaseModel

from panoptes.utils.time import CountdownTimer

from gpio import Gpio


class ShutterState(IntEnum):
    CLOSED = 0
    OPEN = 1


class CameraSettings(BaseSettings):
    port: str
    pin: int
    uid: str | None = None
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

    def __init__(self, camera_settings: CameraSettings | None = None, *args, **kwargs):
        self.camera_settings = camera_settings or CameraSettings(**kwargs)
        self.tether_process: subprocess.Popen | None = None
        self.output_dir: Path | None = None
        self.exposure_timer: CountdownTimer | None = None
        self.gpio = Gpio(self.camera_settings.pin)

    @property
    def is_exposing(self) -> bool:
        return self.exposure_timer is not None and not self.exposure_timer.expired()

    @property
    def is_tethered(self) -> bool:
        return self.tether_process is not None and self.tether_process.poll() is None

    @property
    def shutter_state(self):
        return ShutterState(self.gpio.state)

    def open_shutter(self):
        """Opens the shutter."""
        self.gpio.on()

    def close_shutter(self):
        """Closes the shutter."""
        self.gpio.off()

    def take_picture(self, exptime: float = 1.0):
        """Takes a picture with the camera."""
        print(f'Exposing for {exptime=} seconds at {dt.utcnow()}.')
        self.exposure_timer = CountdownTimer(exptime)
        self.open_shutter()
        sleep(exptime)
        self.close_shutter()
        self.exposure_timer = None
        print(f'Finished exposing at {dt.utcnow()}.')

    def take_sequence(self,
                      exptime: float,
                      num_exposures: int = 1,
                      readout_time: float = 0.0):
        """Take a sequence of exposures."""
        for i in range(num_exposures):
            print(f'Exposure: {i + 1}/{num_exposures} Exptime: {exptime} Interval: {readout_time}')
            self.take_picture(exptime)
            sleep(readout_time)
            print(f'Finished exposure: {i + 1}/{num_exposures}')

    def run_command(self, command: GphotoCommand) -> dict:
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

    def start_tether(self,
                     output_dir: Path = Path('.'),
                     filename_pattern: str | None = None
                     ):
        """Starts a gphoto2 tether and saves images to the given directory."""
        filename_pattern = filename_pattern or self.camera_settings.filename_pattern
        self.output_dir = f'{output_dir.as_posix()}/{filename_pattern}'
        print(f'Starting gphoto2 tether for {self} with {self.output_dir=}')

        full_command = self._build_gphoto2_command(['--filename', self.output_dir,
                                                    '--capture-tethered'])

        print(f'Starting gphoto2 tether for {self} using {self.output_dir=}')
        self.tether_process = subprocess.Popen(full_command)

        # The cameras need a second to connect.
        sleep(1)

    def stop_tether(self):
        """Stop gphoto tether process."""
        print(f'Stopping gphoto2 tether for {self}')
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
                self.output_dir = None

    def download_images(self,
                        output_dir: Path = Path('.'),
                        filename_pattern: str | None = None,
                        only_new: bool = True,
                        ) -> List[Path]:
        """Download the most recent image from the camera."""
        filename_pattern = filename_pattern or self.camera_settings.filename_pattern
        self.output_dir = f'{output_dir.as_posix()}/{filename_pattern}'
        print(f'Downloading images for {self} with {self.output_dir=}')

        cmd_args = ['--get-all-files',
                    '--recurse',
                    '--filename', self.output_dir
                    ]
        if only_new:
            cmd_args.append('--new')

        command = GphotoCommand(arguments=cmd_args, timeout=600)
        command_output = self.run_command(command)

        files = list()
        if command_output['success']:
            for line in command_output['output']:
                if line.startswith('Saving file as '):
                    recent = line.replace('Saving file as ', '')
                    print(f'Found recent image: {recent}')
                    files.append(Path(recent))

        self.output_dir = None
        return files

    def delete_images(self):
        """Delete all images from the camera."""
        print(f'Deleting images for {self}')
        command = GphotoCommand(arguments=['--delete-all-files --recurse'])
        command_output = self.run_command(command)

        return command_output

    def _build_gphoto2_command(self, command: List[str] | str) -> List[str]:
        full_command = [shutil.which('gphoto2'), '--port', self.camera_settings.port]

        # Turn command into a list if not one already.
        with suppress(AttributeError):
            command = command.split(' ')

        full_command.extend(command)

        return full_command

    def __str__(self):
        msg = f'Camera {self.camera_settings.uid} on {self.camera_settings.port}'
        if self.is_exposing:
            msg += f'  [EXPOSING: {self.exposure_timer}]'
        if self.is_tethered:
            msg += f' [TETHERED: {self.tether_process.pid}]'

        return msg
