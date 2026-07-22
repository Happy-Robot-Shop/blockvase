# Simulated ASIC chain: no UART/GPIO: CPU searches nonces; Stratum ↔ DATUM path stays real.

from __future__ import annotations

import logging
import queue

from . import board


class _DummySerial:
    def write(self, data: bytes) -> int:
        return len(data)

    def read(self, size: int = 1) -> bytes:
        return b""

    @property
    def timeout(self) -> float:
        return 0.0

    @timeout.setter
    def timeout(self, _: float) -> None:
        pass


class SimulatedHardware(board.Board):
    def __init__(self, cfg: dict):
        self.config = dict(cfg or {})
        self.config.setdefault("name", "Simulated")
        self._serial = _DummySerial()

    def set_fan_speed(self, channel: int, speed: float) -> None:  # noqa: ARG002
        pass

    def read_temperature(self) -> float:  # noqa: D401
        return float(self.config.get("board_temp_c", 55))

    def read_temperature_and_voltage(self) -> dict:
        t_base = float(self.config.get("board_temp_c", 55))
        v = self.config.get("vdomain_mv", [1200, 1200, 1200, 1200])
        if not isinstance(v, (list, tuple)) or len(v) < 4:
            v = [1200, 1200, 1200, 1200]
        return {
            "temp": [t_base, t_base + 2.5, None, None],
            "voltage": [int(v[i]) if i < len(v) else 1200 for i in range(4)],
        }

    def set_led(self, state: bool) -> None:  # noqa: ARG002
        pass

    def reset_func(self, state=None) -> None:  # noqa: ARG002
        pass

    def shutdown(self) -> None:
        logging.info("Simulated miner board shutdown")

    def serial_port(self) -> _DummySerial:
        return self._serial


class SimClockManager:
    """Enough of ClockManager shape for REST /clock endpoints on simulate builds."""

    def __init__(self, sim_chip: SimulatedASICs, frequency: float, num_asics: int):
        self.bm1366 = sim_chip
        self.num_asics = int(num_asics)
        fq = float(frequency or 485)
        self.clocks: list[float] = [fq for _ in range(self.num_asics)]

    def set_clock(self, asic_id: int, freq: float) -> None:
        fq = float(freq)
        logging.info("(simulated) set_clock id=%s -> %s", asic_id, fq)
        if asic_id == -1:
            self.clocks = [fq for _ in range(self.num_asics)]
        elif 0 <= asic_id < self.num_asics:
            self.clocks[asic_id] = fq

    def get_clock(self, asic_id: int) -> float | list[float]:
        if asic_id == -1:
            return list(self.clocks)
        return self.clocks[asic_id]


class SimulatedASICs:
    """Duck-type BM1366 for job dispatch; send_work queues work IDs for CPU mining thread."""

    def __init__(self, pending: queue.Queue[int]):
        self._pending = pending
        self.clock_manager: SimClockManager | None = None
        self.serial_tx_func = lambda *_: None
        self.serial_rx_func = lambda *_: None
        self.reset_func = lambda *_: None
        self._difficulty_hint: int = 256

    def ll_init(self, serial_tx_func, serial_rx_func, reset_func):
        self.serial_tx_func = serial_tx_func  # noqa: ARG002 unused in sim path
        self.serial_rx_func = serial_rx_func
        self.reset_func = reset_func

    def send_hash_frequency2(self, *_args, **_kwargs) -> None:
        """REST clock API → ClockManager; no-op."""

    def init(self, frequency: float, chips_expected: int, chips_enabled=None, ramp_config=None):  # noqa: ARG002
        n = int(chips_expected) if chips_expected else 1
        self.clock_manager = SimClockManager(self, frequency, n)
        logging.info(
            "Simulated ASIC init: claiming %s chip(s) at ~%s MHz nominal (CPU will hash shares).",
            n,
            frequency,
        )
        return n

    def set_job_difficulty_mask(self, difficulty: int) -> None:
        self._difficulty_hint = max(256, int(difficulty))

    def send_work(self, t) -> None:
        logging.debug("sim: queued work packet id=%02x", getattr(t, "id", -1))
        self._pending.put(int(t.id))

    def get_job_id_from_result(self, job_id: int) -> int:
        return job_id & 0xF8

    def get_job_id(self, job_id: int) -> int:
        return ((job_id << 3) & 0x7F) + 0x08

    def try_get_temp_from_response(self, _response):
        return (None, None)
