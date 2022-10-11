from camera import Camera, CameraSettings, ShutterState
from gpio import Gpio
import logging


class GpioShutterCameraSettings(CameraSettings):
    pin: int


class GpioShutterCamera(Camera):
    """A camera that is controlled by a GPIO pin."""

    def __init__(self, camera_settings: GpioShutterCameraSettings | None = None, *args, **kwargs):
        super().__init__(camera_settings, *args, **kwargs)
        self.gpio = Gpio(self.camera_settings.pin)

    @property
    def shutter_state(self):
        return ShutterState(self.gpio.state)

    def open_shutter(self):
        """Opens the shutter."""
        logging.debug(f'Opening shutter via {self.camera_settings.pin}.')
        self.gpio.on()

    def close_shutter(self):
        """Closes the shutter."""
        logging.debug(f'Closing shutter via {self.camera_settings.pin}.')
        self.gpio.off()
