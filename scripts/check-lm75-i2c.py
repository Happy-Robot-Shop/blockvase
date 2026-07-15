#!/usr/bin/env python3
"""
Read an LM75-class temperature sensor (12-bit layout) without touching UART/GPIO.
Tries likely Raspberry Pi buses; default address 0x48 (matches piaxe config).

Usage:
  python3 scripts/check-lm75-i2c.py
  python3 scripts/check-lm75-i2c.py --bus 13 --address 0x48
Exit 0 if at least one bus returns valid data for the configured address.

On Raspberry Pi 5, `i2cdetect` scans on adapters `i2c-13` / `i2c-14` often show a hex in
almost every cell. That pattern is notorious for **false positives** on RP1. Real checks are
SMBus reads (this script) or `i2ctransfer -y BUS w1@0x48 0x00 r2`, errno 121 / Remote I/O
means no ACK for that transaction despite a busy-looking detector grid.
"""

from __future__ import annotations

import argparse
import os
import sys


def lm75_like_celsius(lo: int, hi: int) -> float:
    """Same 12-bit decode as piaxe read_temperature_and_voltage."""
    raw = (lo << 4) | (hi >> 4)
    if raw > 2047:
        raw -= 4096
    return raw * 0.0625


def try_bus(bus: int, addr7: int) -> tuple[float, list[int]]:
    import smbus  # Debian: python3-smbus

    if not os.path.exists(f"/dev/i2c-{bus}"):
        raise FileNotFoundError(f"No /dev/i2c-{bus}")
    smb = smbus.SMBus(bus)
    pair = smb.read_i2c_block_data(addr7, 0, 2)
    if len(pair) < 2:
        raise RuntimeError(f"Expected 2 bytes, got {pair!r}")
    return lm75_like_celsius(pair[0], pair[1]), pair


def main() -> int:
    p = argparse.ArgumentParser(description="Probe LM75-like sensor at address 0x48 on I2C.")
    p.add_argument(
        "--bus",
        type=int,
        default=None,
        help="Explicit I2C bus number (/dev/i2c-N). Omit to try common Pi buses.",
    )
    p.add_argument(
        "--address",
        type=lambda x: int(x, 0),
        default=0x48,
        help="7-bit address (default 0x48).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Only print PASS/FAIL and temperature line.",
    )
    args = p.parse_args()
    buses = [args.bus] if args.bus is not None else []
    if not buses:
        for cand in (1, 13, 14, 21, 22):
            if os.path.exists(f"/dev/i2c-{cand}"):
                buses.append(cand)

    if not buses:
        print("No /dev/i2c-* buses found.", file=sys.stderr)
        return 1

    ok_any = False
    for b in buses:
        try:
            c, raw = try_bus(b, args.address)
            ok_any = True
            detail = (
                f"PASS: bus {b} addr 0x{args.address:02x}: "
                f"raw_bytes=[{raw[0]:#04x},{raw[1]:#04x}] temp≈ {c:.2f} °C"
            )
            print(detail)
        except OSError as e:
            errno = getattr(e, "errno", None)
            if not args.quiet:
                print(f"FAIL: bus {b} addr 0x{args.address:02x}: {e} (errno={errno})", file=sys.stderr)
        except Exception as e:
            if not args.quiet:
                print(f"FAIL: bus {b} addr 0x{args.address:02x}: {e}", file=sys.stderr)

    if ok_any:
        if not args.quiet:
            print("\nInterpretation: an LM75-class device ACKed register 0 and returned plausible data.")
        return 0

    bus_txt = ", ".join(str(x) for x in buses)
    a = args.address
    print(
        f"\nNo response at 0x{a:02x} on scanned buses ({bus_txt}).\n"
        "Likely causes: sensor not populated/powered, wrong bus (set --bus), incorrect address,\n"
        "bad solder on SDA/SCL/I2C pull-ups, or I2C multiplexing differs from PiAxe schematic.\n\n"
        f"Pi 5 reminder: If `i2cdetect` showed 0x{a:02x} in what looks like a full grid,\n"
        "that RP1 adapter scan pattern is unreliable (many false positives). Real probes are SMBus reads\n"
        "(this script) or, equivalently,\n\n"
        f"  i2ctransfer -y <bus> w1@0x{a:02x} 0x00 r2\n\n"
        "If both fail with Remote I/O error, there is still no ACK for a real LM75 transaction. \n"
        "treat the sensor path as absent or electrically broken rather than trusting `i2cdetect` alone.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
