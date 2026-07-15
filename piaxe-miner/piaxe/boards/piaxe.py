# piaxe
import logging
import os
import serial
import time

_gpio_import_error = None
try:
    import RPi.GPIO as GPIO
except ImportError as e:
    GPIO = None  # type: ignore[assignment, misc]
    _gpio_import_error = e

try:
    from rpi_hardware_pwm import HardwarePWM
except ImportError as e:
    HardwarePWM = None  # type: ignore[assignment, misc]
    _gpio_import_error = _gpio_import_error or e

try:
    import smbus
except ImportError as e:
    smbus = None  # type: ignore[assignment, misc]
    _gpio_import_error = _gpio_import_error or e

from . import board

class RPiHardware(board.Board):
    def __init__(self, config):
        missing = []
        if GPIO is None:
            missing.append("RPi.GPIO")
        if HardwarePWM is None:
            missing.append("rpi_hardware_pwm")
        if smbus is None:
            missing.append("smbus")
        if missing:
            raise ImportError(
                "Missing Pi hardware packages for BM1366 ("
                + ", ".join(missing)
                + "). In the Blockvase venv run: pip install -r requirements.txt "
                "(install-mining-stack does this)."
            ) from _gpio_import_error

        # Setup GPIO
        GPIO.setmode(GPIO.BOARD)  # Use Physical pin numbering

        # Load settings from config
        self.config = config
        self.sdn_pin = self.config['sdn_pin']
        self.pgood_pin = self.config['pgood_pin']
        self.nrst_pin = self.config['nrst_pin']
        self.led_pin = self.config['led_pin']
        self.lm75_address = self.config['lm75_address']

        # Initialize GPIO Pins
        GPIO.setup(self.sdn_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.pgood_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(self.nrst_pin, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(self.led_pin, GPIO.OUT, initial=GPIO.LOW)

        # Pi 4 and earlier: usually /dev/i2c-1. Pi 5 / RP1: commonly i2c-13 or i2c-14.
        if self.config.get("i2c_bus") is not None:
            i2c_n = int(self.config["i2c_bus"])
        else:
            i2c_n = None
            for cand in (1, 13, 14):
                if os.path.exists(f"/dev/i2c-{cand}"):
                    i2c_n = cand
                    break
            if i2c_n is None:
                i2c_n = 1
        try:
            self._bus = smbus.SMBus(i2c_n)
        except OSError as e:
            raise OSError(
                f"Could not open /dev/i2c-{i2c_n} ({e}). "
                "Enable I2C, or set `i2c_bus` under `piaxe:` in config.yml "
                "(Pi 5 is often 13)."
            ) from e
        logging.info("PiAxe SMBus: /dev/i2c-%s (LM75 0x%02x)", i2c_n, int(self.config["lm75_address"]))

        pwm = HardwarePWM(pwm_channel=0, hz=self.config['pwm_hz'])
        pwm.start(self.config['pwm_duty_cycle'])

        # Initialize serial communication
        self._serial_port = serial.Serial(
            port=self.config['serial_port'],
            baudrate=115200,    # Set baud rate to 115200
            bytesize=serial.EIGHTBITS,     # Number of data bits
            parity=serial.PARITY_NONE,     # No parity
            stopbits=serial.STOPBITS_ONE,  # Number of stop bits
            timeout=1                      # Set a read timeout
        )

        GPIO.output(self.sdn_pin, True)

        while (not self._is_power_good()):
            print("power not good ... waiting ...")
            time.sleep(1)

    def _is_power_good(self):
        # Active-low PGOOD: LOW = rail OK, HIGH = not ready (pin has pull-up).
        return not GPIO.input(self.pgood_pin)

    def set_fan_speed(self, channel, speed):
        pass

    def read_temperature_and_voltage(self):
        data = self._bus.read_i2c_block_data(self.lm75_address, 0, 2)
        # Convert the data to 12-bits
        temp = (data[0] << 4) | (data[1] >> 4)
        # Convert to a signed 12-bit value
        if temp > 2047:
            temp -= 4096

        # Convert to Celsius
        celsius = temp * 0.0625
        return {
            "temp": [celsius, None, None, None],
            "voltage": [None, None, None, None],
        }

    def set_led(self, state):
        GPIO.output(self.led_pin, True if state else False)

    def reset_func(self):
        # BM1366 NRSTI is active-low: pulse low, then release high for normal operation.
        GPIO.output(self.nrst_pin, False)
        time.sleep(0.5)
        GPIO.output(self.nrst_pin, True)
        time.sleep(0.5)


    def shutdown(self):
        # disable buck converter
        logging.info("shutdown miner ...")
        GPIO.output(self.sdn_pin, False)
        self.set_led(False)

    def serial_port(self):
        return self._serial_port