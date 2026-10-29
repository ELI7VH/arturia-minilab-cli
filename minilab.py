#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Elijah Lucian / Lucian Labs
"""MiniLab 3 User-preset CLI. No firmware updates, global writes, or factory-slot writes."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

HEADER = (0, 32, 107, 127, 66)
ROOT = Path(__file__).resolve().parent
KEYS = tuple(json.loads((ROOT / 'preset-keys.json').read_text()))
PARAM_KEYS = {int(k.split('_')[1]): k for k in KEYS}


def user_slot(value):
    if type(value) is not int:
        raise ValueError('User slot must be an integer')
    if not 1 <= value <= 5:
        raise ValueError('User slot must be 1–5; factory/DAW memories are excluded')
    return value


def address(slot, param):
    user_slot(slot)
    if type(param) is not int or param not in PARAM_KEYS:
        raise ValueError('Unsupported preset parameter')
    return (slot + 2, 64, param & 127, param >> 7)


def packet(op, slot, param, value=None):
    if op not in (32, 33):
        raise ValueError('Only preset parameter reads/writes are supported')
    data = HEADER + (op,) + address(slot, param)
    if op == 33:
        if type(value) is not int or not 0 <= value <= 127:
            raise ValueError('Preset values must be integers 0–127')
        data += (value,)
    elif value is not None:
        raise ValueError('Read request cannot contain a value')
    return data


def no_duplicates(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError(f'Duplicate JSON key: {k}')
        result[k] = v
    return result


def validate_preset(data):
    if not isinstance(data, dict) or data.get('device') != 'MiniLab 3':
        raise ValueError('Expected native MiniLab 3 preset JSON')
    if data.get('version') != '1.2.0':
        raise ValueError('This prototype supports preset format version 1.2.0 only')
    if set(data) != set(KEYS) | {'device', 'version'}:
        raise ValueError('Expected the complete 319-field MiniLab 3 preset; unknown/missing fields refused')
    if any(type(data[k]) is not int or not 0 <= data[k] <= 127 for k in KEYS):
        raise ValueError('All 319 preset values must be integers 0–127')
    return data


def load_preset(path):
    return validate_preset(json.loads(Path(path).read_text(), object_pairs_hook=no_duplicates))


def changes(expected, actual):
    return [(k, actual[k], expected[k]) for k in KEYS if expected[k] != actual[k]]


def save_json(path, data):
    """Exclusive creation: an existing recovery file must never be overwritten."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        f.write(json.dumps(data, indent=2) + '\n')
    return path


class MiniLab:
    def __init__(self, port=None, timeout=.8):
        import mido
        self.mido, self.timeout = mido, timeout
        ins, outs = mido.get_input_names(), mido.get_output_names()
        candidates = [p for p in ins if re.fullmatch(r'Minilab\s*3 MIDI', p, re.I) and p in outs]
        if port is None:
            if len(candidates) != 1:
                raise ValueError(f'Connect one MiniLab 3. Matching MIDI ports: {candidates}')
            port = candidates[0]
        if port not in candidates or candidates.count(port) != 1:
            raise ValueError('Choose the MiniLab 3 MIDI port; DIN THRU, ALV and MCU/HUI are excluded')
        self.port = port
        self.input = mido.open_input(port)
        try:
            self.output = mido.open_output(port)
            self.identity = self.identify()
        except BaseException:
            self.input.close()
            if hasattr(self, 'output'):
                self.output.close()
            raise

    def close(self):
        self.input.close()
        self.output.close()

    def exchange(self, data, match):
        for _ in self.input.iter_pending():
            pass
        self.output.send(self.mido.Message('sysex', data=data))
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            for message in self.input.iter_pending():
                if message.type == 'sysex':
                    reply = tuple(message.data)
                    if match(reply):
                        return reply
            time.sleep(.001)
        raise TimeoutError(f'No matching MiniLab reply to {list(data)}')

    def identify(self):
        r = self.exchange((126, 127, 6, 1),
                          lambda r: len(r) == 15 and r[0] == 126 and r[2:4] == (6, 2))
        if r[4:7] != (0, 32, 107) or r[7:9] != (2, 0) or r[9:11] not in ((4, 2), (4, 4)):
            raise ValueError(f'Unsupported MiniLab identity: {list(r)}')
        return {'port': self.port, 'sysex': list(r), 'firmware': '.'.join(map(str, reversed(r[11:15])))}

    def read(self, slot, param):
        req = packet(32, slot, param)
        prefix = HEADER + (33,) + address(slot, param)
        for attempt in range(2):
            try:
                r = self.exchange(req, lambda r: len(r) == 11 and r[:10] == prefix)
                return r[10]
            except TimeoutError:
                if attempt:
                    raise

    def snapshot(self, slot):
        data = {'device': 'MiniLab 3', 'version': '1.2.0'}
        for param, key in PARAM_KEYS.items():
            data[key] = self.read(slot, param)
        return validate_preset(data)

    def store(self, slot):
        # Arturia preset-store command; only the five User memories are reachable.
        user_slot(slot)
        self.output.send(self.mido.Message('sysex', data=HEADER + (6, slot + 2)))
        time.sleep(.2)

    def write(self, slot, param, value):
        self.output.send(self.mido.Message('sysex', data=packet(33, slot, param, value)))
        time.sleep(.02)
        actual = self.read(slot, param)
        if actual != value:
            raise RuntimeError(f'Parameter {param} readback {actual}, expected {value}')


def upload(dev, preset, slot, backup_dir, dry_run=False):
    """Validate and save recovery evidence before the first write; verify and roll back failures."""
    validate_preset(preset)
    user_slot(slot)
    if dev.identity['firmware'] != '1.2.0.69':
        raise ValueError('Uploads currently validated for firmware 1.2.0.69 only; reads remain available')
    before = dev.snapshot(slot)
    diff = changes(preset, before)
    if dry_run:
        return {'dry_run': True, 'slot': slot, 'changes': diff}
    backup_dir = Path(backup_dir).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=False)
    before_path = save_json(backup_dir / 'before.minilab3', before)
    save_json(backup_dir / 'identity.json', dev.identity)
    save_json(backup_dir / 'requested.minilab3', preset)
    save_json(backup_dir / 'plan.json', {'slot': slot, 'changes': diff,
        'before_sha256': hashlib.sha256(before_path.read_bytes()).hexdigest()})
    touched = []
    try:
        for key, old, new in diff:
            touched.append((key, old))
            dev.write(slot, int(key.split('_')[1]), new)
        if diff:
            dev.store(slot)
        after = dev.snapshot(slot)
        if changes(preset, after):
            raise RuntimeError('Full preset readback differs after upload')
        save_json(backup_dir / 'after.minilab3', after)
    except BaseException as error:
        try:
            for key, old in reversed(touched):
                dev.write(slot, int(key.split('_')[1]), old)
            if touched:
                dev.store(slot)
            rolled_back = dev.snapshot(slot)
            if changes(before, rolled_back):
                raise RuntimeError('Rollback readback differs')
            save_json(backup_dir / 'rollback.minilab3', rolled_back)
            message = 'Original preset restored and verified'
        except BaseException as rollback_error:
            message = f'Rollback incomplete: {rollback_error}. Recovery preset: {before_path}'
        save_json(backup_dir / 'failure.json', {'error': str(error), 'recovery': message})
        raise RuntimeError(f'Upload failed: {error}. {message}') from error
    result = {'slot': slot, 'changed_fields': len(diff), 'verified_fields': len(KEYS),
              'backup': str(before_path), 'firmware_update': False,
              'store_command_sent': bool(diff),
              'verification': 'full slot readback after store; power-cycle persistence requires a separate check'}
    save_json(backup_dir / 'receipt.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="minilab", description=__doc__)
    parser.add_argument('--port', help='Exact MiniLab 3 MIDI port; automatically selected when unique')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='Detect and identify the connected MiniLab (read only)')
    back = sub.add_parser('backup', help='Export a complete native .minilab3 preset (read only)')
    back.add_argument('--slot', type=int, required=True, choices=range(1, 6))
    back.add_argument('--out', type=Path, required=True)
    diff = sub.add_parser('diff', help='Compare file against stored User slot (read only)')
    diff.add_argument('file', type=Path)
    diff.add_argument('--slot', type=int, required=True, choices=range(1, 6))
    preset = sub.add_parser('preset').add_subparsers(dest='action', required=True)
    put = preset.add_parser('upload', help='Upload preset mappings with automatic backup and readback')
    put.add_argument('file', type=Path)
    put.add_argument('--slot', type=int, required=True, choices=range(1, 6))
    put.add_argument('--dry-run', action='store_true')
    put.add_argument('--backup-dir', type=Path)
    args = parser.parse_args(argv)
    # Reject malformed files before opening any MIDI port.
    data = load_preset(args.file) if hasattr(args, 'file') else None
    dev = MiniLab(args.port)
    try:
        if args.command == 'list':
            result = dev.identity
        elif args.command == 'backup':
            p = save_json(args.out, dev.snapshot(args.slot))
            result = {'slot': args.slot, 'saved': str(p), 'fields': len(KEYS)}
        elif args.command == 'diff':
            result = {'slot': args.slot, 'changes': changes(data, dev.snapshot(args.slot))}
        else:
            stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
            directory = args.backup_dir or Path.home() / 'Documents' / 'MiniLab Backups' / f'{stamp}-user{args.slot}-{uuid.uuid4().hex[:6]}'
            result = upload(dev, data, args.slot, directory, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        dev.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(f'minilab: {error}', file=sys.stderr)
        sys.exit(1)
