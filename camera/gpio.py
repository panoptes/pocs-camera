from .gphoto2 import Camera as CameraClass
from .gphoto2 import CameraSettings, ShutterState
import logging
from enum import IntEnum
from time import perf_counter

try:
    import lgpio
except ImportError:
    lgpio = None
    logging.warning('lgpio is not installed. '
                    'Please install it with `sudo apt install python3-lgpio`')


class PinState(IntEnum):
    OFF = 0
    ON = 1


class Camera(CameraClass):
    """A camera that is controlled by a GPIO pin."""

    def __init__(self, pin: int, camera_settings: CameraSettings | None = None, *args, **kwargs):
        super().__init__(camera_settings, *args, **kwargs)
        self.pin = pin
        self.gpio = Gpio(self.pin)
        self._exposure_start_time = None

    @property
    def is_exposing(self) -> bool:
        return self.gpio.state == PinState.ON

    @property
    def exposure_timer(self) -> float:
        if self._exposure_start_time is None:
            return 0
        return perf_counter() - self._exposure_start_time

    @exposure_timer.setter
    def exposure_timer(self, value: float):
        """This is a no-op, but is required for compatibility with the Camera class."""
        self._exposure_start_time = None

    @property
    def shutter_state(self):
        return ShutterState(self.gpio.state)

    def open_shutter(self):
        """Opens the shutter."""
        logging.debug(f'Opening shutter via {self.pin}.')
        self.gpio.on()
        self._exposure_start_time = perf_counter()

    def close_shutter(self):
        """Closes the shutter."""
        logging.debug(f'Closing shutter via {self.pin}.')
        self.gpio.off()
        self._exposure_start_time = None


class Gpio:
    def __init__(self, pin):
        logging.debug(f'Initializing GPIO pin {pin}.')
        self.pin = pin
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.h, self.pin)
        logging.info(f'Opened GPIO pin {self.pin} for OUTPUT.')

    def on(self):
        """Turns on the GPIO pin."""
        logging.debug(f'Turning on GPIO pin {self.pin}.')
        lgpio.gpio_write(self.h, self.pin, PinState.ON)

    def off(self):
        """Turns off the GPIO pin."""
        logging.debug(f'Turning off GPIO pin {self.pin}.')
        lgpio.gpio_write(self.h, self.pin, PinState.OFF)

    def toggle(self):
        """Toggles the state of the GPIO pin."""
        old_state = self.state
        new_state = PinState(not old_state)
        logging.debug(f'Toggling GPIO pin {self.pin} from {old_state.name} to {new_state.name}.')
        self.state = new_state

    @property
    def state(self):
        """Returns the state of the GPIO pin."""
        state = PinState(lgpio.gpio_read(self.h, self.pin))
        logging.debug(f'GPIO pin {self.pin} is {state}.')
        return state

    @state.setter
    def state(self, new_state: PinState):
        """Sets the state of the GPIO pin."""
        try:
            getattr(self, new_state.name.lower())()
        except AttributeError:
            logging.error(f'Invalid state {new_state}.')

    def __str__(self):
        return f'Gpio({self.pin}) = {self.state}'

    def __del__(self):
        lgpio.gpiochip_close(self.h)
