import logging
import warnings
from enum import IntEnum

try:
    import lgpio
except ImportError:
    warnings.warn('lgpio is not installed. '
                  'Please install it with `sudo apt install python3-lgpio`')


class PinState(IntEnum):
    OFF = 0
    ON = 1


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
