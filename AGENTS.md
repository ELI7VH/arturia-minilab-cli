# MiniLab CLI

Read ../lucian-utils/AGENTS.md for shared rules when available.

- Preserve upstream protocol attribution and GPL-3.0-or-later licensing.
- Only User slots 1–5 may be written. Factory/DAW slots, global settings and firmware updates are excluded.
- Back up before writes; verify all319 fields after store; report incomplete recovery explicitly.
- Keep private controller backups outside Git.
- Run `python3 -m unittest discover -s . -p "test_*.py" -v`.
- Hardware claims require live evidence; readback does not prove physical knob emission.
