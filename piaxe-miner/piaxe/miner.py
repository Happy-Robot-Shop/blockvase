# piaxe-miner board/ASIC control (GPL-3.0; see ../LICENSE.txt).
# Modified for Blockvase (Happy Robot Shop), 2026: soft-fail board/GPIO/I2C and
# ASIC init so LM75/REST monitoring can continue without hashing. See repository
# README Credits for the full modification list.

import serial
import time
import logging
import random
import copy
import os
import math
import yaml
import json
from pathlib import Path

import threading
import queue
from shared import shared

from . import ssd1306
from . import bm1366
from . import influx
from . import discord
from . import rest
from . import smartplug

from .boards import piaxe
from .boards import qaxe
from .boards import bitcrane
from .boards import flex4axe
from .boards import zeroxaxe
from .boards import simulate_hardware

try:
    from .ssd1306 import SSD1306
except:
    pass

class Job(shared.Job):
    def __init__(
        self,
        job_id,
        prevhash,
        coinb1,
        coinb2,
        merkle_branches,
        version,
        nbits,
        ntime,
        extranonce1,
        extranonce2_size,
        max_nonce=0x7fffffff,
    ):
        super().__init__(job_id, prevhash, coinb1, coinb2, merkle_branches, version, nbits, ntime, extranonce1, extranonce2_size, max_nonce)


class BM1366Miner:
    def __init__(self, config, address, network):
        self.config = config

        self.current_job = None
        self.current_work = None
        self.hardware = None
        self.asics = None
        self.serial_port = None

        self._read_index = 0
        self._write_index = 0
        self._buffer = bytearray([0] * 64)

        self._internal_id = 0
        self._latest_work_id = 0
        self._jobs = dict()
        self.last_response = time.time()

        self.tracker_send = list()
        self.tracker_received = list()

        self.job_thread = None
        self.receive_thread = None
        self.temp_thread = None
        self.display_thread = None
        self.job_lock = threading.Lock()
        self.serial_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.new_job_event = threading.Event()
        self.led_thread = None
        self.led_event = threading.Event()
        self.network = network
        self.address = address

        self.last_job_time = time.time()
        self.last_response = time.time()

        self.found_hashes = dict()
        self.found_timestamps = list()

        self.shares = list()
        # Cumulative share work for lifetime / EMA hashrate (avoids noisy fixed 600s window).
        self._hash_work_total = 0
        self._hash_last_ts = None
        self._hash_rate_ema = 0.0
        self._hash_ema_tau_sec = 1200.0  # ~20 min time-constant
        self.stats = influx.Stats()

        self.display = SSD1306(self.stats)

        self._simulate_work_queue = None
        self.simulate_hardware_thread = None

        self.miner = self.config['miner']
        self.verify_solo = self.config.get('verify_solo', False)
        self.debug_bm1366 = self.config.get("debug_bm1366", False)
        self.asic_initialized = False
        board_cfg = self.config.get(self.miner, {})
        self.max_board_temp_c = float(board_cfg.get("max_board_temp_c", 70.0))

        # Thermal frequency governor: throttle/recover around ambient swings.
        self._current_asic_mhz = 0.0
        self._asic_frequency_target_mhz = float(board_cfg.get("asic_frequency", 0.0) or 0.0)
        self._thermal_gov_enabled = bool(board_cfg.get("thermal_governor_enabled", False))
        self._thermal_gov_min_mhz = float(board_cfg.get("thermal_governor_min_mhz", 300.0))
        self._thermal_gov_step_mhz = float(
            board_cfg.get(
                "thermal_governor_step_mhz",
                board_cfg.get("frequency_ramp_step_mhz", 6.25),
            )
        )
        throttle_default = self.max_board_temp_c - 3.0
        self._thermal_gov_throttle_temp_c = float(
            board_cfg.get("thermal_governor_throttle_temp_c", throttle_default)
        )
        recover_default = self._thermal_gov_throttle_temp_c - 4.0
        self._thermal_gov_recover_temp_c = float(
            board_cfg.get("thermal_governor_recover_temp_c", recover_default)
        )
        self._thermal_gov_step_interval_sec = float(
            board_cfg.get("thermal_governor_step_interval_sec", 45.0)
        )
        self._thermal_gov_last_step_ts = 0.0

    def shutdown(self):
        # signal the threads to end
        self.stop_event.set()

        # stop influx
        if self.influx:
            self.influx.shutdown()

        # stop smartplug
        if self.smartplug:
            self.smartplug.shutdown()

        # join all threads
        for t in [
            self.job_thread,
            self.receive_thread,
            self.temp_thread,
            self.display_thread,
            self.led_thread,
            self.uptime_counter_thread,
            self.alerter_thread,
            self.simulate_hardware_thread,
        ]:
            if t is not None:
                t.join(5)

        if self.hardware is not None:
            self.hardware.shutdown()

    def get_name(self):
        if self.hardware is None:
            return f"{self.miner}-unavailable"
        return self.hardware.get_name()

    def get_user_agent(self):
        return f"{self.get_name()}/0.1"

    def _frequency_ramp_config(self):
        board_cfg = self.config.get(self.miner, {})
        ramp = {
            "start_mhz": board_cfg.get("frequency_ramp_start_mhz", 56.25),
            "step_mhz": board_cfg.get("frequency_ramp_step_mhz", 6.25),
            "step_delay_sec": board_cfg.get("frequency_ramp_step_delay_sec", 0.1),
        }
        pause_temp = board_cfg.get("frequency_ramp_pause_temp_c")
        if pause_temp is not None and self.hardware is not None:
            ramp["pause_temp_c"] = float(pause_temp)
            ramp["cooldown_temp_c"] = float(board_cfg.get("frequency_ramp_cooldown_temp_c", 62.0))
            ramp["temp_reader"] = self.hardware.read_temperature_and_voltage
        cool = self._active_thermal_cooldown()
        if cool:
            start = float(cool.get("start_mhz") or getattr(self, "_thermal_gov_min_mhz", None) or ramp["start_mhz"])
            ramp["start_mhz"] = start
            logging.info("thermal cooldown active: frequency ramp starts at %.2f MHz", start)
        return ramp

    def _start_rest_api(self):
        rest_config = self.config.get("rest_api", None)
        self.rest_api = None
        if rest_config is not None and rest_config.get("enabled", False):
            self.rest_api = rest.RestAPI(rest_config, self, self.stats)
            self.rest_api.run()

    def init(self):
        self.hardware = None
        self.asics = None
        self.influx = None
        self.smartplug = None
        self.alerter_thread = None
        self.uptime_counter_thread = None
        self.receive_thread = None
        self.job_thread = None
        self.simulate_hardware_thread = None
        self.display_thread = None

        try:
            if self.miner == "bitcrane":
                self.hardware = bitcrane.BitcraneHardware(self.config[self.miner])
                self.asics = bm1366.BM1366()
            elif self.miner == "piaxe":
                self.hardware = piaxe.RPiHardware(self.config[self.miner])
                self.asics = bm1366.BM1366()
            elif self.miner == "simulate":
                self._simulate_work_queue = queue.Queue()
                sim_cfg = self.config.get("simulate", {})
                self.hardware = simulate_hardware.SimulatedHardware(sim_cfg)
                self.asics = simulate_hardware.SimulatedASICs(self._simulate_work_queue)
            elif self.miner == "qaxe":
                self.hardware = qaxe.QaxeHardware(self.config[self.miner])
                self.asics = bm1366.BM1366()
            elif self.miner == "qaxe+":
                self.hardware = qaxe.QaxeHardware(self.config[self.miner])
                self.asics = bm1366.BM1368()
            elif self.miner == "flex4axe":
                self.hardware = flex4axe.Flex4AxeHardware(self.config[self.miner])
                self.asics = bm1366.BM1366()
            elif self.miner == "0xaxe":
                self.hardware = zeroxaxe.ZeroxAxe(self.config[self.miner])
                self.asics = bm1366.BM1366()
            else:
                raise Exception("unknown miner: %s" % self.miner)

            self.serial_port = self.hardware.serial_port()
            self.asics.ll_init(self._serial_tx_func, self._serial_rx_func, self.hardware.reset_func)
        except Exception as e:
            # Board/GPIO/I2C failure: keep process up for portal; no hashing.
            logging.error(
                "Board/hardware init failed: %s. Continuing without ASIC (REST stats only).",
                e,
            )
            self.hardware = None
            self.asics = None
            self.serial_port = None
            self.asic_initialized = False
            self.uptime_counter_thread = threading.Thread(target=self._uptime_counter_thread, daemon=True)
            self.uptime_counter_thread.start()
            self._start_rest_api()
            return

        # LM75 / REST for portal dashboard: start before ASIC init so temp works when BM1366 is down.
        self.temp_thread = threading.Thread(target=self._monitor_temperature, daemon=True)
        self.temp_thread.start()

        self.uptime_counter_thread = threading.Thread(target=self._uptime_counter_thread, daemon=True)
        self.uptime_counter_thread.start()

        self.led_thread = threading.Thread(target=self._led_thread, daemon=True)
        self.led_thread.start()

        self._start_rest_api()

        # default is: enable all chips
        chips_enabled = self.config[self.miner].get("chips_enabled", None)

        max_retries = 5  # Maximum number of attempts
        chip_counter = 0
        board_cfg = self.config.get(self.miner, {})
        init_max_temp = board_cfg.get("asic_init_max_temp_c")
        if init_max_temp is not None and self.hardware is not None:
            init_max_temp = float(init_max_temp)
            while not self.stop_event.is_set():
                try:
                    board_t = self.hardware.read_temperature_and_voltage()["temp"][0]
                except Exception as ex:
                    logging.warning("Pre-init temp read failed: %s", ex)
                    time.sleep(2.0)
                    continue
                if board_t is None or board_t <= init_max_temp:
                    break
                logging.info(
                    "Waiting for board cooldown before ASIC init: %.1f°C > %.1f°C",
                    board_t,
                    init_max_temp,
                )
                time.sleep(3.0)

        def _enable_asic_power_safe():
            timeout_sec = float(board_cfg.get("asic_power_good_timeout_sec", 8.0))
            abort_temp = board_cfg.get("asic_power_abort_temp_c")
            if abort_temp is None:
                # Leave a few degrees of headroom under the hard thermal shutdown.
                abort_temp = max(60.0, self.max_board_temp_c - 7.0)
            else:
                abort_temp = float(abort_temp)
            self.hardware.enable_asic_power(
                timeout_sec=timeout_sec,
                abort_temp_c=abort_temp,
            )
            time.sleep(float(board_cfg.get("asic_power_settle_sec", 3.0)))

        # currently the qaxe+ needs this loop :see-no-evil:
        for attempt in range(max_retries):
            try:
                if (
                    self.miner == "piaxe"
                    and self.hardware is not None
                    and hasattr(self.hardware, "enable_asic_power")
                ):
                    _enable_asic_power_safe()
                chip_counter = self.asics.init(
                    self.hardware.get_asic_frequency(),
                    self.hardware.get_chip_count(),
                    chips_enabled,
                    self._frequency_ramp_config(),
                )
                print("Initialization successful.")
                self.asic_initialized = True
                self._asic_frequency_target_mhz = float(self.hardware.get_asic_frequency())
                self._current_asic_mhz = self._asic_frequency_target_mhz
                with self.stats.lock:
                    self.stats.asic_frequency_mhz = self._current_asic_mhz
                    self.stats.asic_frequency_target_mhz = self._asic_frequency_target_mhz
                if self._thermal_gov_enabled:
                    logging.info(
                        "thermal governor enabled: target=%.2f MHz min=%.2f MHz "
                        "throttle≥%.1f°C recover≤%.1f°C step=%.2f interval=%.0fs",
                        self._asic_frequency_target_mhz,
                        self._thermal_gov_min_mhz,
                        self._thermal_gov_throttle_temp_c,
                        self._thermal_gov_recover_temp_c,
                        self._thermal_gov_step_mhz,
                        self._thermal_gov_step_interval_sec,
                    )
                break
            except Exception as e:
                logging.error("Attempt %d: ASIC init/power failed: %s", attempt + 1, e)

                # only retry on 1368s
                if not isinstance(self.asics, bm1366.BM1368):
                    if (
                        attempt < max_retries - 1
                        and self.miner == "piaxe"
                        and self.hardware is not None
                    ):
                        logging.info("Retrying ASIC init after cool-down power cycle...")
                        self.hardware.shutdown()
                        time.sleep(float(board_cfg.get("asic_power_retry_sec", 8.0)))
                        continue
                    logging.error("ASIC init failed; LM75/REST monitoring continues without hashing.")
                    if self.miner == "piaxe" and self.hardware is not None:
                        self.hardware.shutdown()
                    break

                if attempt < max_retries - 1:
                    time.sleep(1)  # Wait before the next attempt
                else:
                    logging.error("Max retries reached. ASIC init failed; LM75/REST monitoring continues.")

        if self.asic_initialized:
            logging.info(f"{chip_counter} chips were found!")
            self.set_difficulty(512)
            self.extranonce2_interval = self.config[self.miner]["extranonce2_interval"]

            if self.miner != "simulate":
                self.receive_thread = threading.Thread(target=self._receive_thread, daemon=True)
                self.receive_thread.start()
            else:
                self.receive_thread = None
                self.simulate_hardware_thread = threading.Thread(target=self._simulate_work_thread, daemon=True)
                self.simulate_hardware_thread.start()

            self.job_thread = threading.Thread(target=self._job_thread, daemon=True)
            self.job_thread.start()
        else:
            logging.warning("Running without ASIC: board temperature via LM75 only.")
            self.receive_thread = None
            self.job_thread = None
            self.simulate_hardware_thread = None

        influx_config = self.config.get('influx', None)
        self.influx = None
        if influx_config is not None and influx_config.get('enabled', False):
            stats_name = "mainnet_stats"
            if self.network == shared.BitcoinNetwork.TESTNET:
                stats_name = "testnet_stats"
            elif self.network == shared.BitcoinNetwork.REGTEST:
                stats_name = "regtest_stats"

            self.influx = influx.Influx(influx_config, self.stats, stats_name)
            try:
                self.influx.load_last_values()
            except Exception as e:
                logging.error("we really don't want to start without previous influx values: %s", e)
                if self.hardware is not None:
                    self.hardware.shutdown()
                os._exit(0)

            # start writing thread after values were loaded
            self.influx.start()

        smartplug_config = self.config.get('smartplug', None)
        self.smartplug = None
        if smartplug_config is not None and smartplug_config.get('enabled', False):
            if not self.influx:
                logging.error("influx not enabled, skipping smartplug module")
            else:
                self.smartplug = smartplug.Tasmota(smartplug_config)
                self.influx.add_stats_callback(self.smartplug.add_smart_plug_energy_data)
                self.smartplug.start()

        alerter_config = self.config.get("alerter", None)
        self.alerter_thread = None
        if alerter_config is not None and alerter_config.get("enabled", False):
            if alerter_config["type"] == "discord-webhook":
                self.alerter = discord.DiscordWebhookAlerter(alerter_config)
                self.alerter_thread = threading.Thread(target=self._alerter_thread)
                self.alerter_thread.start()
            else:
                raise Exception(f"unknown alerter: {alerter_config['type']}")

        i2c_config = self.config.get("i2c_display", None)
        if i2c_config is not None and i2c_config.get("enabled", False):
            self.display_thread = threading.Thread(target=self._display_update, daemon=True)
            self.display_thread.start()


    def _uptime_counter_thread(self):
        logging.info("uptime counter thread started ...")
        while not self.stop_event.is_set():
            with self.stats.lock:
                self.stats.total_uptime += 1
                self.stats.uptime += 1
                self._refresh_hash_rate_lifetime_locked()
            time.sleep(1)

        logging.info("uptime counter thread ended ...")

    def _alerter_thread(self):
        logging.info("Alerter thread started ...")
        self.alerter.alert("MINER", "started")
        while not self.stop_event.is_set():
            self.alerter.alert_if("NO_JOB", "no new job for more than 5 minutes!", (time.time() - self.last_job_time) > 5*60)
            self.alerter.alert_if("NO_RESPONSE", "no ASIC response for more than 5 minutes!", (time.time() - self.last_response) > 5*60)
            time.sleep(1)

        self.alerter.alert("MINER", "shutdown")
        logging.info("Alerter thread ended ...")

    def _display_update(self):
        logging.info("display update ...")
        self.display.init()
        while not self.stop_event.is_set():
                self.display.update()
                time.sleep(2)
        logging.info("display update ended ...")

    def _led_thread(self):
        logging.info("LED thread started ...")
        led_state = True
        while not self.stop_event.is_set():
            if self.hardware is None:
                time.sleep(1)
                continue
            # if for more than 5 minutes no new job is received
            # we flash the light faster
            if time.time() - self.last_job_time > 5*60 or \
                time.time() - self.last_response > 5*60:
                led_state = not led_state
                self.hardware.set_led(led_state)
                time.sleep(0.25)
                continue

            # this gets triggered in 2s intervals
            # .wait() doesn't work reliably because it happens
            # that the submit method hangs forever and the
            # event wouldn't be fired then
            if self.led_event.is_set():
                self.led_event.clear()
                led_state = not led_state
                self.hardware.set_led(led_state)
                continue

            time.sleep(0.25)

        logging.info("LED thread ended ...")

    def _monitor_temperature(self):
        while not self.stop_event.is_set():
            if self.hardware is None:
                time.sleep(1.5)
                continue
            try:
                temp = self.hardware.read_temperature_and_voltage()
            except Exception as ex:
                logging.warning("LM75 / board sensor read failed: %s", ex)
                time.sleep(1.5)
                continue

            # trigger measurement of metrics
            if self.asic_initialized and isinstance(self.asics, bm1366.BM1368):
                try:
                    self.asics.request_temps()
                except Exception as ex:
                    logging.debug("ASIC temp request skipped: %s", ex)

            with self.stats.lock:
                self.stats.temp = temp["temp"][0]
                self.stats.temp2 = temp["temp"][1]
                self.stats.temp3 = temp["temp"][2]
                self.stats.temp4 = temp["temp"][3]
                self.stats.vdomain1 = temp["voltage"][0]
                self.stats.vdomain2 = temp["voltage"][1]
                self.stats.vdomain3 = temp["voltage"][2]
                self.stats.vdomain4 = temp["voltage"][3]

                # inject asic temps into the temp dict for display
                temp['asic_temp'] = [
                    self.stats.asic_temp1,
                    self.stats.asic_temp2,
                    self.stats.asic_temp3,
                    self.stats.asic_temp4
                ]

            logging.info("temperature and voltage: %s", str(temp))

            board_temp = temp["temp"][0]
            if board_temp is not None:
                self._thermal_governor_tick(board_temp)

            for i in range(0, 4):
                if temp["temp"][i] is not None and temp["temp"][i] > self.max_board_temp_c:
                    logging.error(
                        "too hot (%.1f°C > %.1f°C), shutting down ...",
                        temp["temp"][i],
                        self.max_board_temp_c,
                    )
                    self._record_thermal_trip(temp["temp"][i])
                    self.hardware.shutdown()
                    os._exit(1)

            time.sleep(1.5)

    def _thermal_trip_path(self):
        return Path(os.environ.get("BLOCKVASE_THERMAL_TRIP_PATH", "/var/lib/blockvase/thermal-trip.json"))

    def _record_thermal_trip(self, board_temp):
        """Persist cooldown so restarts do not immediately re-ramp to max MHz."""
        try:
            path = self._thermal_trip_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            board_cfg = self.config.get(self.miner, {})
            cooldown_sec = float(board_cfg.get("thermal_trip_cooldown_sec", 300.0))
            start_mhz = float(getattr(self, "_thermal_gov_min_mhz", 300.0) or 300.0)
            payload = {
                "tripped_at": time.time(),
                "resume_after": time.time() + max(60.0, cooldown_sec),
                "board_temp_c": float(board_temp),
                "start_mhz": start_mhz,
            }
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            logging.warning(
                "thermal trip recorded: cooldown until +%.0fs, restart ramp from %.2f MHz",
                cooldown_sec,
                payload["start_mhz"],
            )
        except Exception as ex:
            logging.warning("failed to record thermal trip: %s", ex)

    def _active_thermal_cooldown(self):
        try:
            path = self._thermal_trip_path()
            if not path.is_file():
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
            resume_after = float(raw.get("resume_after", 0) or 0)
            if time.time() >= resume_after:
                try:
                    path.unlink()
                except OSError:
                    pass
                return None
            return raw
        except Exception:
            return None

    def _thermal_governor_tick(self, board_temp):
        """Step ASIC clock down near trip / up when cool. Hard trip remains last resort."""
        if not self._thermal_gov_enabled or not self.asic_initialized:
            return
        if self.asics is None or not hasattr(self.asics, "clock_manager"):
            return
        clock_manager = getattr(self.asics, "clock_manager", None)
        if clock_manager is None:
            return

        now = time.time()
        target = float(self._asic_frequency_target_mhz)
        current = float(self._current_asic_mhz)
        if target <= 0 or current <= 0:
            return

        step = float(self._thermal_gov_step_mhz)
        min_mhz = float(self._thermal_gov_min_mhz)
        throttle_temp = float(self._thermal_gov_throttle_temp_c)
        recover_temp = float(self._thermal_gov_recover_temp_c)
        interval = float(self._thermal_gov_step_interval_sec)
        emergency_temp = self.max_board_temp_c - 1.0

        with self.stats.lock:
            self.stats.asic_frequency_mhz = current
            self.stats.asic_frequency_target_mhz = target

        want_down = board_temp >= throttle_temp and current > min_mhz + 1e-6
        want_up = board_temp <= recover_temp and current < target - 1e-6
        emergency = board_temp >= emergency_temp and current > min_mhz + 1e-6

        if not want_down and not want_up and not emergency:
            return

        # Prefer throttle over recover if thresholds ever overlap.
        if want_down or emergency:
            if not emergency and (now - self._thermal_gov_last_step_ts) < interval:
                return
            new_mhz = max(min_mhz, current - step)
            if new_mhz >= current - 1e-9:
                return
            direction = "down"
        else:
            if (now - self._thermal_gov_last_step_ts) < interval:
                return
            new_mhz = min(target, current + step)
            if new_mhz <= current + 1e-9:
                return
            direction = "up"

        try:
            clock_manager.set_clock(-1, new_mhz)
        except Exception as ex:
            logging.warning("thermal governor set_clock failed: %s", ex)
            return

        logging.info(
            "thermal governor: %.2f → %.2f MHz (temp %.1f°C, %s)",
            current,
            new_mhz,
            board_temp,
            direction,
        )
        self._current_asic_mhz = new_mhz
        self._thermal_gov_last_step_ts = now
        with self.stats.lock:
            self.stats.asic_frequency_mhz = new_mhz
            self.stats.asic_frequency_target_mhz = target

    def _serial_tx_func(self, data):
        with self.serial_lock:
            total_sent = 0
            while total_sent < len(data):
                sent = self.serial_port.write(data[total_sent:])
                if sent == 0:
                    raise RuntimeError("Serial connection broken")
                total_sent += sent
            if self.debug_bm1366:
                logging.debug("-> %s", bytearray(data).hex())

    def _serial_rx_func(self, size, timeout_ms):
        self.serial_port.timeout = timeout_ms / 1000.0

        data = self.serial_port.read(size)
        bytes_read = len(data)

        if self.debug_bm1366 and bytes_read > 0:
            logging.debug("serial_rx: %d", bytes_read)
            logging.debug("<- %s", data.hex())

        return data if bytes_read > 0 else None

    def cleanup_duplicate_finds(self):
        current_time = time.time()

        # clean up dict, delete old hashes, counts elements to pop from the list
        remove_first_n=0
        for timestamp, hash_key in self.found_timestamps:
            if current_time - timestamp > 600:
                #logging.debug(f"removing {hash_key} from found_hashes dict")
                if hash_key in self.found_hashes:
                    del self.found_hashes[hash_key]
                else:
                    pass
                    #logging.debug(f"{hash_key} not in dict")
                remove_first_n += 1
            else:
                break

        # pop elements
        #logging.debug(f"removing first {remove_first_n} element(s) of found_timestamps list")
        for i in range(0, remove_first_n):
            self.found_timestamps.pop(0)


    def _lifetime_hash_rate_gh_locked(self):
        """Lifetime GH/s from cumulative share work / uptime. Caller holds stats.lock."""
        if self._hash_work_total <= 0:
            return 0.0
        uptime = max(int(self.stats.uptime), 1)
        return (self._hash_work_total / uptime) / 1e9

    def _refresh_hash_rate_lifetime_locked(self):
        """Keep lifetime (and display) current as uptime advances between shares."""
        lifetime_gh = self._lifetime_hash_rate_gh_locked()
        self.stats.hashing_speed_lifetime = lifetime_gh
        if self._hash_rate_ema <= 0 and lifetime_gh > 0:
            self._hash_rate_ema = lifetime_gh
        elif self._hash_rate_ema > 0 and lifetime_gh > 0:
            # Gentle pull toward lifetime when shares are sparse
            self._hash_rate_ema = 0.997 * self._hash_rate_ema + 0.003 * lifetime_gh
        self.stats.hashing_speed_ema = self._hash_rate_ema
        self.stats.hashing_speed = (
            self._hash_rate_ema if self._hash_rate_ema > 0 else lifetime_gh
        )

    def _update_hash_rates(self, difficulty):
        """
        Update lifetime + EMA hashrate after a valid share.

        Lifetime: total share-work / uptime (stable truth).
        EMA: time-constant smoothed inter-share rate (~20 min), clamped vs lifetime
        so high pool difficulty doesn't produce 2x phantom spikes.
        Primary stats.hashing_speed follows the EMA (lifetime until EMA seeds).
        """
        now = time.time()
        work = int(difficulty) << 32
        with self.stats.lock:
            self._hash_work_total += work
            lifetime_gh = self._lifetime_hash_rate_gh_locked()
            self.stats.hashing_speed_lifetime = lifetime_gh

            if self._hash_last_ts is None or self._hash_rate_ema <= 0:
                self._hash_rate_ema = lifetime_gh
            else:
                dt = max(now - self._hash_last_ts, 1e-3)
                instant_gh = (work / dt) / 1e9
                # Cap absurd bursts (two shares milliseconds apart)
                cap = max(lifetime_gh * 3.0, 750.0)
                instant_gh = min(instant_gh, cap)
                alpha = 1.0 - math.exp(-dt / self._hash_ema_tau_sec)
                alpha = min(max(alpha, 0.02), 0.35)
                self._hash_rate_ema = (
                    alpha * instant_gh + (1.0 - alpha) * self._hash_rate_ema
                )

            self._hash_last_ts = now
            self.stats.hashing_speed_ema = self._hash_rate_ema
            self.stats.hashing_speed = (
                self._hash_rate_ema if self._hash_rate_ema > 0 else lifetime_gh
            )
            logging.debug(
                "\033[32mhash rate: ema=%.1f lifetime=%.1f GH/s\033[0m",
                self.stats.hashing_speed_ema,
                self.stats.hashing_speed_lifetime,
            )
            return self.stats.hashing_speed

    def hash_rate(self, time_period=600):
        """Backward-compatible helper; returns EMA/lifetime GH/s (time_period unused)."""
        with self.stats.lock:
            self._refresh_hash_rate_lifetime_locked()
            return self.stats.hashing_speed

    def _set_target(self, target):
        self._target = '%064x' % target

    def set_difficulty(self, difficulty):
        # restrict to min 256
        difficulty = max(difficulty, 256)

        self._difficulty = difficulty
        self._set_target(shared.calculate_target(difficulty))
        self.asics.set_job_difficulty_mask(difficulty)

        with self.stats.lock:
            self.stats.difficulty = difficulty

    def set_submit_callback(self, cb):
        self.submit_cb = cb

    def accepted_callback(self):
        with self.stats.lock:
            self.stats.accepted += 1

    def not_accepted_callback(self):
        with self.stats.lock:
            self.stats.not_accepted += 1

    def _receive_thread(self):
        logging.info('receiving thread started ...')
        mask_nonce = 0x00000000
        mask_version = 0x00000000

        while not self.stop_event.is_set():
            byte = self._serial_rx_func(11, 100)

            if not byte:
                continue

            for i in range(0, len(byte)):
                self._buffer[self._write_index % 64] = byte[i]
                self._write_index += 1

            if self._write_index - self._read_index >= 11 and self._buffer[self._read_index % 64] == 0xaa and self._buffer[(self._read_index + 1) % 64] == 0x55:
                data = bytearray([0] * 11)
                for i in range(0, 11):
                    data[i] = self._buffer[self._read_index % 64]
                    self._read_index += 1

                #if self.debug_bm1366:
                #    logging.debug("<- %s", bytes(data).hex())

                asic_result = bm1366.AsicResult().from_bytes(bytes(data))
                if not asic_result or not asic_result.nonce:
                    continue

                # temperature response
                (temp_value, temp_id) = self.asics.try_get_temp_from_response(asic_result)
                if temp_value:
                    logging.debug(f"temp for chip {temp_id}: {temp_value}")

                    attribute_name = f"asic_temp{temp_id+1}"
                    with self.stats.lock:
                        setattr(self.stats, attribute_name, temp_value * 0.171342 - 299.5144)

                    continue

                with self.job_lock:
                    self.last_response = time.time()
                    result_job_id = self.asics.get_job_id_from_result(asic_result.job_id)
                    logging.debug("work received %02x", result_job_id)

                    if result_job_id not in self._jobs:
                        logging.debug("internal jobid %d not found", result_job_id)
                        continue

                    saved_job = self._jobs[result_job_id]
                    job = saved_job['job']
                    work = saved_job['work']
                    difficulty = saved_job['difficulty']

                    if result_job_id != work.id:
                        logging.error("mismatch ids")
                        continue

                    result = dict(
                        job_id = job._job_id,
                        extranonce2 = job._extranonce2, #shared.int_to_hex32(job._extranonce2),
                        ntime = job._ntime,
                        nonce = shared.int_to_hex32(asic_result.nonce),
                        version = shared.int_to_hex32(shared.reverse_uint16(asic_result.version) << 13),
                    )


                    is_valid, hash, zeros = shared.verify_work(difficulty, job, result)
                    network_target, network_zeros = shared.nbits_to_target(job._nbits)
                    pool_target, pool_zeros = shared.get_network_target(difficulty)

                    logging.debug("network-target: %s (%d)", network_target, network_zeros)
                    logging.debug("pool-target:    %s (%d)", pool_target, pool_zeros)
                    logging.debug("found hash:     %s (%d)", hash, zeros)

                    # detect duplicates
                    duplicate = hash in self.found_hashes

                    self.cleanup_duplicate_finds()

                    # save hash in dict
                    self.found_hashes[hash] = True
                    self.found_timestamps.append((time.time(), hash))

                    # some debug info
                    #logging.debug(f"{len(self.found_hashes)} in found_hashes dict, {len(self.found_timestamps)} in found_timestamps list")

                    if duplicate:
                        logging.warn("found duplicate hash!")

                    if hash < network_target:
                        logging.info("!!! it seems we found a block !!!")

                    # the hash isn't completly wrong but isn't lower than the target
                    # the asic uses power-of-two targets but the pool might not (eg ckpool)
                    # we should just pretend it didn't happen and not count it^^
                    if not is_valid and zeros >= pool_zeros:
                        logging.info("ignoring hash because higher than pool target")
                        continue


                    if is_valid:
                        mask_nonce |= asic_result.nonce
                        mask_version |= asic_result.version << 13

                        logging.debug(f"mask_nonce:   %s (%08x)", shared.int_to_bin32(mask_nonce, 4), mask_nonce)
                        logging.debug(f"mask_version: %s (%08x)", shared.int_to_bin32(mask_version, 4), mask_version)
                        x_nonce = (asic_result.nonce & 0x0000fc00) >> 10
                        logging.debug(f"result from asic {x_nonce}")

                    with self.stats.lock:
                        if hash < network_target:
                            self.stats.blocks_found += 1
                            self.stats.total_blocks_found += 1

                        if duplicate:
                            self.stats.duplicate_hashes += 1

                        self.stats.invalid_shares += 1 if not is_valid else 0
                        self.stats.valid_shares += 1 if is_valid else 0

                        # don't add to shares if it's invalid or it's a duplicate
                        if is_valid and not duplicate:
                            self.shares.append((1, difficulty, time.time()))

                        hash_difficulty = shared.calculate_difficulty_from_hash(hash)
                        self.stats.best_difficulty = max(self.stats.best_difficulty, hash_difficulty)
                        self.stats.total_best_difficulty = max(self.stats.total_best_difficulty, hash_difficulty)

                    if is_valid and not duplicate:
                        self._update_hash_rates(difficulty)

                    # restart miner with new extranonce2
                    #self.new_job_event.set() TODO

                # submit result without lock on the job!
                # we don't submit invalid hashes or duplicates
                if not is_valid or duplicate:
                    # if its invalid it would be rejected
                    # we don't try it but we can count it to not_accepted
                    self.not_accepted_callback()
                    logging.error("invalid result!")
                    continue


                logging.info("valid result")
                if not self.submit_cb:
                    logging.error("no submit callback set")
                elif not self.submit_cb(result):
                    self.stats.pool_errors += 1


    def _simulate_work_thread(self):
        """CPU brute-force shares through the same validation/submit path as UART ASIC responses."""
        logging.info("simulated ASIC hashing thread started ...")
        pending = self._simulate_work_queue
        if pending is None:
            logging.error("simulate: work queue missing")
            return

        sim_cfg = self.config.get("simulate") or {}
        max_nonce_scan = int(sim_cfg.get("max_nonces_per_work", 3_500_000))
        res_version = shared.int_to_hex32(shared.reverse_uint16(0) << 13)

        while not self.stop_event.is_set():
            try:
                work_id = pending.get(timeout=0.5)
            except queue.Empty:
                continue

            with self.job_lock:
                bundle = self._jobs.get(work_id)

            if not bundle:
                logging.debug("sim: stale work id %02x", work_id)
                continue

            job = bundle["job"]
            difficulty = bundle["difficulty"]
            t_scan0 = time.time()
            submitted_share = False

            for nonce in range(0, max_nonce_scan):
                if self.stop_event.is_set():
                    break

                result = dict(
                    job_id=job._job_id,
                    extranonce2=job._extranonce2,
                    ntime=job._ntime,
                    nonce=shared.int_to_hex32(nonce),
                    version=res_version,
                )

                is_valid, hash_hex, zeros = shared.verify_work(difficulty, job, result)
                network_target, network_zeros = shared.nbits_to_target(job._nbits)
                pool_target, pool_zeros = shared.get_network_target(difficulty)

                duplicate = hash_hex in self.found_hashes

                self.cleanup_duplicate_finds()

                self.found_hashes[hash_hex] = True
                self.found_timestamps.append((time.time(), hash_hex))

                if duplicate:
                    logging.warning("sim: duplicate hash")

                if not is_valid and zeros >= pool_zeros:
                    logging.debug("sim: ignoring hash because higher than pool target")
                    continue

                if nonce > 0 and nonce % 200_000 == 0:
                    elapsed = max(1e-6, time.time() - t_scan0)
                    approx_gh = nonce / elapsed / 1e9
                    with self.stats.lock:
                        self.stats.hashing_speed = approx_gh

                self.last_response = time.time()

                with self.stats.lock:
                    if hash_hex < network_target:
                        self.stats.blocks_found += 1
                        self.stats.total_blocks_found += 1

                    if duplicate:
                        self.stats.duplicate_hashes += 1

                    self.stats.invalid_shares += 1 if not is_valid else 0
                    self.stats.valid_shares += 1 if is_valid else 0

                    if is_valid and not duplicate:
                        self.shares.append((1, difficulty, time.time()))

                    hash_difficulty = shared.calculate_difficulty_from_hash(hash_hex)
                    self.stats.best_difficulty = max(self.stats.best_difficulty, hash_difficulty)
                    self.stats.total_best_difficulty = max(self.stats.total_best_difficulty, hash_difficulty)

                if is_valid and not duplicate:
                    self._update_hash_rates(difficulty)

                if not is_valid or duplicate:
                    self.not_accepted_callback()
                    logging.debug("sim: invalid or duplicate (not submitting)")
                    continue

                logging.info("sim: valid share (CPU)")
                if not self.submit_cb:
                    logging.error("no submit callback set")
                elif not self.submit_cb(result):
                    with self.stats.lock:
                        self.stats.pool_errors += 1

                submitted_share = True
                self.led_event.set()
                break

            if not submitted_share:
                logging.debug(
                    "sim: exhausted %d nonces for job %s (still connected; more work arrives from Stratum)",
                    max_nonce_scan,
                    job._job_id,
                )

        logging.info("simulated ASIC hashing thread ended ...")


    def _job_thread(self):
        logging.info("job thread started ...")
        current_time = time.time()
        while not self.stop_event.is_set():
            self.new_job_event.wait(self.extranonce2_interval)
            self.new_job_event.clear()

            with self.job_lock:
                if not self.current_job:
                    logging.info("no job ...")
                    time.sleep(1)
                    continue

                extranonce2 = random.randint(0, 2**31-1)
                logging.debug("new extranonce2 %08x", extranonce2)
                self.current_job.set_extranonce2(extranonce2)

                self._internal_id += 1
                self._latest_work_id = self.asics.get_job_id(self._internal_id)

                work = bm1366.WorkRequest()
                logging.debug("new work %02x", self._latest_work_id)
                work.create_work(
                    self._latest_work_id,
                    0x00000000,
                    shared.hex_to_int(self.current_job._nbits),
                    shared.hex_to_int(self.current_job._ntime),
                    shared.reverse_bytes(shared.hex_to_bytes(self.current_job._merkle_root)),
                    shared.reverse_bytes(shared.hex_to_bytes(self.current_job._prevhash)),
                    shared.hex_to_int(self.current_job._version)
                )
                self.current_work = work

                # make deepcopies
                self._jobs[self._latest_work_id] = {
                    'job': copy.deepcopy(self.current_job),
                    'work': copy.deepcopy(self.current_work),
                    'difficulty': self._difficulty
                }

                self.led_event.set()

                self.asics.send_work(work)

        logging.info("job thread ended ...")

    def clean_jobs(self):
        with self.job_lock:
            logging.info("cleaning jobs ...")
            self._jobs = dict()
            self.current_job = None

    def start_job(self, job):
        logging.info("starting new job %s", job._job_id)

        self.last_job_time = time.time()
        with self.job_lock:
            self.current_job = job

            if self.verify_solo:
                try:
                    # only decode when verify_solo is enabled
                    coinb = job.deserialize_coinbase()
                    if coinb['height'] is not None:
                        logging.debug("mining for block %d", coinb['height'])

                    is_solo, value_our, value_total = shared.verify_solo(self.address, coinb)
                    logging.debug("solo mining verification passed! reward: %d", value_our)
                except Exception as e:
                    logging.error("verify_solo error: %s", e)
            else:
                logging.debug("solo mining not verified!")

            #logging.debug(json.dumps(job.deserialize_coinbase(), indent=4))


            self.new_job_event.set()
