# Blockvase PiAxe HAT

Open-hardware BM1366 Raspberry Pi HAT used in Blockvase, derived from the OSMU [PiAxe](https://github.com/shufps/piaxe) design by [shufps](https://github.com/shufps).

## What changed vs upstream PiAxe

Blockvase redesigns power delivery so a **single USB-C port** powers the entire hardware stack (Raspberry Pi + miner HAT), instead of the stock PiAxe power arrangement.

## Files in this directory

Schematics, board files, and related manufacturing outputs for the Blockvase HAT live here. Software that drives the board is separate: see [`piaxe-miner/`](../../piaxe-miner/) and the Credits section in the root [README](../../README.md).

## Credits

- Upstream design: [shufps/piaxe](https://github.com/shufps/piaxe) / [OSMU](https://osmu.wiki/)
- PiAxe manufacturing ecosystem: [D-Central Technologies](https://d-central.tech/)
