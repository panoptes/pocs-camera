from enum import IntEnum

try:
    import lgpio
except ImportError:
    raise ImportError('lgpio is not installed. '
                      'Please install it with `sudo apt install python3-lgpio`')


class PinState(IntEnum):
    OFF = 0
    ON = 1


class Gpio:
    def __init__(self, pin):
        self.pin = pin
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.h, self.pin)

    def on(self):
        """Turns on the GPIO pin."""
        lgpio.gpio_write(self.h, self.pin, PinState.ON)

    def off(self):
        """Turns off the GPIO pin."""
        lgpio.gpio_write(self.h, self.pin, PinState.OFF)

    @property
    def state(self):
        """Returns the state of the GPIO pin."""
        return PinState(lgpio.gpio_read(self.h, self.pin))

    @state.setter
    def state(self, new_state: PinState):
        """Sets the state of the GPIO pin."""
        lgpio.gpio_write(self.h, self.pin, new_state.value)

    def __str__(self):
        return f'Gpio({self.pin}) = {self.state}'

    def __del__(self):
        lgpio.gpiochip_close(self.h)
