#!/usr/bin/env python3
"""
Probe whether a BM1366 chain responds on the PiAxe UART path (same discovery
sequence as piaxe-miner send_init, without PLL ramp or Stratum).

Exit codes:
  0  chip count matches expected (ASIC responding)
  1  usage / precondition (e.g. blockvase-miner still running → GPIO busy)
  2  BM1366 did not respond with the expected chip count
  3  hardware bootstrap failed (GPIO, I²C, serial, etc.)

Run via check-asic.sh so the piaxe-miner venv (system-site-packages) is used on Pi 5+.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
PIAXE_DIR = PROJECT_ROOT / "piaxe-miner"

# PiAxe package imports expect CWD inside piaxe-miner like pyminer.py
if str(PIAXE_DIR) not in sys.path:
    sys.path.insert(0, str(PIAXE_DIR))


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as e:
        raise SystemExit(f"Missing PyYAML ({e}); use the miner venv: scripts/check-asic.sh") from e
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise SystemExit(f"Cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid YAML in {path}")
    return data


PREAMBLE_BE = 0xAA55
KNOWN_CHIP_IDS = {
    0x1366: "BM1366",
    0x1368: "BM1368",
    0x1370: "BM1370",
}


def _analyze_response(data: bytes, piaxe_match_hex: str) -> dict:
    """Classify one UART read the same way ESP-Miner does vs piaxe's substring check."""
    from piaxe.crc_functions import crc5

    hex_full = data.hex()
    out: dict = {
        "len": len(data),
        "hex": hex_full,
        "piaxe_substring_match": piaxe_match_hex in hex_full,
    }
    if len(data) < 2:
        out["verdict"] = "too_short"
        return out

    preamble = (data[0] << 8) | data[1]
    out["preamble"] = f"0x{preamble:04x}"
    out["preamble_ok"] = preamble == PREAMBLE_BE
    out["tx_echo_guess"] = data[0:2] == bytes([0x55, 0xAA])

    if len(data) >= 4:
        chip_id = (data[2] << 8) | data[3]
        out["chip_id"] = f"0x{chip_id:04x}"
        out["chip_family"] = KNOWN_CHIP_IDS.get(chip_id)
    else:
        chip_id = None
        out["chip_id"] = None
        out["chip_family"] = None

    if len(data) >= 3:
        out["crc5_ok"] = crc5(data[2:]) == 0
    else:
        out["crc5_ok"] = False

    if all(b == 0 for b in data):
        out["verdict"] = "all_zeros_no_asic_uart"
    elif out["preamble_ok"] and chip_id == 0x1366 and out["crc5_ok"]:
        out["verdict"] = "valid_bm1366_chip_id"
    elif out["preamble_ok"] and chip_id in KNOWN_CHIP_IDS and out["crc5_ok"]:
        out["verdict"] = f"valid_{KNOWN_CHIP_IDS[chip_id].lower()}_chip_id"
    elif out["tx_echo_guess"]:
        out["verdict"] = "likely_tx_echo"
    elif out["preamble_ok"] and chip_id is not None:
        out["verdict"] = "preamble_ok_chip_or_crc_mismatch"
    else:
        out["verdict"] = "unrecognized"

    return out


def _dump_chip_scan_responses(chips, rx_f, piaxe_match_hex: str, max_reads: int = 64) -> None:
    """Send chip-ID read and print every raw 11-byte response with parse diagnostics."""
    from piaxe import bm1366

    # Repeat the same preamble as send_init so this scan matches miner enumeration.
    for _ in range(3):
        chips.send(
            bm1366.TYPE_CMD | bm1366.GROUP_ALL | bm1366.CMD_WRITE,
            [0x00, 0xA4, 0x90, 0x00, 0xFF, 0xFF],
        )

    print("\n--- Raw UART chip-scan dump ---", flush=True)
    print(f"Expected piaxe substring in hex: {piaxe_match_hex!r}", flush=True)
    print("ESP-Miner expects: preamble 0xAA55, chip_id 0x1366 (bytes 2-3), crc5 OK", flush=True)

    chips.send(bm1366.TYPE_CMD | bm1366.GROUP_ALL | bm1366.CMD_READ, [0x00, 0x00])

    seen_hex: dict[str, int] = {}
    valid_esp = 0
    valid_piaxe = 0

    for idx in range(1, max_reads + 1):
        data = rx_f(11, 5000)
        if data is None:
            print(f"  [{idx:02d}] (timeout / no data)", flush=True)
            break

        analysis = _analyze_response(data, piaxe_match_hex)
        if analysis.get("piaxe_substring_match"):
            valid_piaxe += 1
        if analysis.get("verdict") == "valid_bm1366_chip_id":
            valid_esp += 1

        key = analysis["hex"]
        seen_hex[key] = seen_hex.get(key, 0) + 1

        print(
            f"  [{idx:02d}] len={analysis['len']:2d} hex={analysis['hex']} "
            f"verdict={analysis['verdict']} "
            f"preamble={analysis.get('preamble', '?')} "
            f"chip_id={analysis.get('chip_id', '?')} "
            f"crc5_ok={analysis.get('crc5_ok', False)} "
            f"piaxe_match={analysis.get('piaxe_substring_match', False)}",
            flush=True,
        )

    chips.send(bm1366.TYPE_CMD | bm1366.GROUP_ALL | bm1366.CMD_INACTIVE, [0x00, 0x00])

    print("\n--- Summary ---", flush=True)
    print(f"  Unique response patterns: {len(seen_hex)}", flush=True)
    print(f"  Valid by ESP-Miner rules (BM1366): {valid_esp}", flush=True)
    print(f"  Valid by piaxe substring match: {valid_piaxe}", flush=True)
    for pattern, count in sorted(seen_hex.items(), key=lambda item: (-item[1], item[0])):
        sample = bytes.fromhex(pattern)
        verdict = _analyze_response(sample, piaxe_match_hex)["verdict"]
        print(f"  x{count:2d}  {pattern}  ({verdict})", flush=True)
    print("--- end dump ---\n", flush=True)


def _make_serial_funcs(port, debug: bool):
    lock = threading.Lock()

    def tx(data: bytes | bytearray) -> None:
        with lock:
            total = 0
            buf = bytes(data)
            while total < len(buf):
                n = port.write(buf[total:])
                if n == 0:
                    raise RuntimeError("Serial write returned 0 (disconnected)")
                total += n
            if debug:
                logging.debug("tx %s", buf.hex())

    def rx(size: int, timeout_ms: int):
        with lock:
            port.timeout = timeout_ms / 1000.0
            chunk = port.read(size)
            if debug and chunk:
                logging.debug("rx %s", chunk.hex())
            return chunk if len(chunk) > 0 else None

    return tx, rx


GPIO_BUSY_HINT = (
    "\n(GPIO busy: Pi 5 lgpio pins are already claimed, only one program may use PiAxe lines at once.)\n"
    "Often:  sudo systemctl stop blockvase-miner.service\n"
    "Then rerun check-asic.sh. Restart later: sudo systemctl start blockvase-miner.service\n"
)


def _systemd_unit_active(unit: str) -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def main() -> None:
    default_cfg = PIAXE_DIR / "config.blockvase.yml"
    if not default_cfg.is_file():
        default_cfg = PIAXE_DIR / "config.yml"
    parser = argparse.ArgumentParser(
        description="Check BM1366 / PiAxe ASIC responsiveness (UART + chip enumeration)."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=default_cfg,
        help=f"Miner YAML (default: {default_cfg})",
    )
    parser.add_argument(
        "--expected-chips",
        type=int,
        default=None,
        help="Expected BM1366 count (default: piaxe.chips from YAML)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Debug serial traffic on stderr"
    )
    parser.add_argument(
        "--no-temperature",
        action="store_true",
        help="Skip LM75 temperature read via I²C",
    )
    parser.add_argument(
        "--skip-service-check",
        action="store_true",
        help="Allow run while blockvase-miner may be active (usually causes GPIO busy on Pi 5)",
    )
    parser.add_argument(
        "--reset-active-low",
        action="store_true",
        help="Use alternate reset pulse (low then high) for boards where nRST is active-low.",
    )
    parser.add_argument(
        "--dump-responses",
        action="store_true",
        help="After enumeration, print every raw 11-byte UART read with parse diagnostics "
        "(auto-enabled when chip count mismatches).",
    )
    args = parser.parse_args()

    cfg_path = args.config.resolve()
    cfg = _load_yaml(cfg_path)
    miner = cfg.get("miner")
    if miner != "piaxe":
        print(
            f"Warning: miner is {miner!r}, not 'piaxe'; this probe only drives PiAxe/BM1366.",
            file=sys.stderr,
            flush=True,
        )

    p_cfg = cfg.get("piaxe")
    if not isinstance(p_cfg, dict):
        raise SystemExit("[piaxe] section missing in config, cannot probe PiAxe board.")

    expected = args.expected_chips
    if expected is None:
        try:
            expected = int(p_cfg.get("chips", 1))
        except (TypeError, ValueError) as e:
            raise SystemExit(f"Bad piaxe.chips in config: {p_cfg.get('chips')}") from e

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    print(f"Using config {cfg_path}", flush=True)
    print(f"Serial port from YAML: {p_cfg.get('serial_port', '?')}", flush=True)

    if not args.skip_service_check and _systemd_unit_active("blockvase-miner.service"):
        print(
            "blockvase-miner.service is running; stop it first so this script can claim PiAxe GPIO.",
            file=sys.stderr,
            flush=True,
        )
        print("  sudo systemctl stop blockvase-miner.service", file=sys.stderr, flush=True)
        print(
            "Or pass --skip-service-check if you are sure nothing holds those GPIO lines.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)

    os.chdir(PIAXE_DIR)

    from piaxe import bm1366
    from piaxe.boards import piaxe as piaxe_mod

    hardware = None
    serial_port = None
    rc = 3

    try:
        hardware = piaxe_mod.RPiHardware(p_cfg)

        # Useful board-state breadcrumb before any BM1366 traffic.
        try:
            import RPi.GPIO as GPIO
            pgood_raw = GPIO.input(hardware.pgood_pin)
            nrst_raw = GPIO.input(hardware.nrst_pin)
            print(
                f"GPIO state before init: PGOOD(raw)={pgood_raw} NRST(raw)={nrst_raw}",
                flush=True,
            )
        except Exception:
            pass

        reset_fn = hardware.reset_func
        if args.reset_active_low:
            try:
                import RPi.GPIO as GPIO

                def _reset_active_low():
                    GPIO.output(hardware.nrst_pin, False)
                    time.sleep(0.20)
                    GPIO.output(hardware.nrst_pin, True)
                    time.sleep(0.50)

                reset_fn = _reset_active_low
                print("Using alternate reset pulse: nRST low -> high", flush=True)
            except Exception as ex:
                logging.warning("Could not enable alternate reset pulse: %s", ex)

        serial_port = hardware.serial_port()
        chips = bm1366.BM1366()
        tx_f, rx_f = _make_serial_funcs(serial_port, args.verbose)
        chips.ll_init(tx_f, rx_f, reset_fn)

        logging.info("Reset ASIC bridge (hardware.reset_func)...")
        chips.reset()

        logging.info(
            "Sending BM1366 preamble + enumerating chips (idle %d expected)...", expected
        )
        for _ in range(3):
            chips.send(bm1366.TYPE_CMD | bm1366.GROUP_ALL | bm1366.CMD_WRITE, [0x00, 0xA4, 0x90, 0x00, 0xFF, 0xFF])
        actual = chips.count_asic_chips()

        if args.dump_responses or actual != expected:
            _dump_chip_scan_responses(chips, rx_f, chips.chip_id_response)

        if actual == expected:
            print(f"OK: BM1366 responded with {actual} chip(s) (matches expected {expected}).", flush=True)
            rc = 0
        else:
            print(
                f"FAIL: saw {actual} chip ID response(s), expected {expected}. "
                "Check power, UART wiring, serial_port, and ASIC seating.",
                file=sys.stderr,
                flush=True,
            )
            rc = 2

        if not args.no_temperature:
            try:
                readings = hardware.read_temperature_and_voltage()
                temps = readings.get("temp") or []
                t0 = temps[0] if temps else None
                if t0 is not None:
                    print(f"LM75 board temp (approx): {t0:.2f} °C", flush=True)
                else:
                    print("LM75: no temperature in reading (unexpected).", flush=True)
            except OSError as e:
                logging.warning("LM75/I²C read failed: %s", e)

    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        rc = 1
    except Exception as ex:
        logging.exception("%s", ex)
        rc = 3
        print(f"Abort: {ex}", file=sys.stderr, flush=True)
        if "GPIO busy" in str(ex) or "gpio busy" in str(ex).lower():
            print(GPIO_BUSY_HINT, file=sys.stderr, flush=True)

    finally:
        if hardware is not None:
            try:
                hardware.shutdown()
            except Exception as ce:
                logging.warning("hardware.shutdown raised: %s", ce)

    raise SystemExit(rc)


if __name__ == "__main__":
    main()
