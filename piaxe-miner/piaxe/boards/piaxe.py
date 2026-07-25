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

        # HAT 4-pin fan PWM is GPIO18 / physical pin 12 (fan only; buck uses SDN).
        # dtoverlay=pwm leaves the pin in PWM mode with pull-down (reads low) which
        # stops the Noctua. Drive BOARD pin 12 as GPIO high for always-on full speed.
        self._fan_pwm_pin = int(self.config.get("fan_pwm_pin", 12))
        self._force_fan_full()

        # Initialize serial communication
        self._serial_port = serial.Serial(
            port=self.config['serial_port'],
            baudrate=115200,    # Set baud rate to 115200
            bytesize=serial.EIGHTBITS,     # Number of data bits
            parity=serial.PARITY_NONE,     # No parity
            stopbits=serial.STOPBITS_ONE,  # Number of stop bits
            timeout=1                      # Set a read timeout
        )

    def enable_asic_power(self, timeout_sec=8.0, abort_temp_c=None):
        """Enable the buck and wait for PGOOD.

        If PGOOD never asserts, leave SDN high only briefly — a stuck regulator
        loop can heat the board by ~15°C in a few seconds with no hashing.
        """
        GPIO.output(self.sdn_pin, True)
        deadline = time.monotonic() + max(0.5, float(timeout_sec))
        while not self._is_power_good():
            if abort_temp_c is not None:
                try:
                    board_t = self.read_temperature_and_voltage()["temp"][0]
                except Exception as ex:
                    logging.warning("Power-up temp read failed: %s", ex)
                    board_t = None
                if board_t is not None and board_t > float(abort_temp_c):
                    GPIO.output(self.sdn_pin, False)
                    raise RuntimeError(
                        "ASIC power aborted: board %.1f°C > %.1f°C while waiting for PGOOD"
                        % (board_t, float(abort_temp_c))
                    )
            if time.monotonic() >= deadline:
                GPIO.output(self.sdn_pin, False)
                raise TimeoutError(
                    "ASIC power good timed out after %.1fs (buck disabled)" % float(timeout_sec)
                )
            logging.info("power not good ... waiting ...")
            time.sleep(0.5)

    def _is_power_good(self):
        # SiC431 open-drain PG with pull-up to 3V3: HIGH = in regulation.
        # (Do not invert — LED1 on the PGOOD net lights when power is NOT good.)
        return GPIO.input(self.pgood_pin)

    def _release_pwm0(self):
        """Release hardware PWM0 so GPIO18 can be used as a plain output."""
        enable_path = "/sys/class/pwm/pwmchip0/pwm0/enable"
        unexport_path = "/sys/class/pwm/pwmchip0/unexport"
        try:
            if os.path.exists(enable_path):
                with open(enable_path, "w", encoding="utf-8") as f:
                    f.write("0\n")
            if os.path.exists(unexport_path) and os.path.isdir("/sys/class/pwm/pwmchip0/pwm0"):
                with open(unexport_path, "w", encoding="utf-8") as f:
                    f.write("0\n")
        except OSError as ex:
            logging.debug("HAT fan: PWM0 release: %s", ex)

    def _force_fan_full(self):
        """Hold fan PWM pin high so the Noctua runs at full speed."""
        self._release_pwm0()
        try:
            GPIO.setup(self._fan_pwm_pin, GPIO.OUT, initial=GPIO.HIGH)
            GPIO.output(self._fan_pwm_pin, GPIO.HIGH)
            logging.info(
                "HAT fan: BOARD pin %s held HIGH (always-on full speed)",
                self._fan_pwm_pin,
            )
        except Exception as ex:
            logging.warning("HAT fan: could not drive pin %s high: %s", self._fan_pwm_pin, ex)

    def set_fan_speed(self, channel, speed):
        # Always-on cooling: ignore speed requests and keep the pin high.
        self._force_fan_full()

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
        # BM1366 NRSTI is active-low at the chip, but the HAT routes Pi pin 15
        # (RST) through Q1 (BSS138 common-source inverter) to RST_N. Same as
        # upstream PiAxe Q5. GPIO HIGH → chip held in reset; GPIO LOW → runs.
        # Pulse high to assert, then rest low so the ASIC can respond on UART.
        GPIO.output(self.nrst_pin, True)
        time.sleep(0.5)
        GPIO.output(self.nrst_pin, False)
        time.sleep(0.5)


    def shutdown(self):
        # disable buck converter; keep fan PWM high for full-speed cooldown
        logging.info("shutdown miner ...")
        GPIO.output(self.sdn_pin, False)
        self.set_led(False)
        self._force_fan_full()

    def serial_port(self):
        return self._serial_port
