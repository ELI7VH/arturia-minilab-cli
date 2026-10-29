# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import minilab as ml


def preset():
    return {'device': 'MiniLab 3', 'version': '1.2.0', **dict.fromkeys(ml.KEYS, 0)}


class Fake:
    identity = {'firmware': '1.2.0.69'}
    def __init__(self, data=None, fail_param=None, disconnect=False):
        self.data = copy.deepcopy(data or preset())
        self.writes = []
        self.stores = []
        self.fail_param, self.failed, self.disconnect = fail_param, False, disconnect
    def store(self, slot):
        ml.user_slot(slot); self.stores.append(slot)
    def snapshot(self, slot):
        ml.user_slot(slot)
        return dict(self.data)
    def write(self, slot, param, value):
        self.writes.append((slot, param, value))
        self.data[ml.PARAM_KEYS[param]] = value  # may fail after the device accepted data
        if param == self.fail_param and (not self.failed or self.disconnect):
            self.failed = True
            raise TimeoutError('lost reply')


class ProtocolTests(unittest.TestCase):
    def test_schema_complete_and_unique(self):
        self.assertEqual(set(ml.PARAM_KEYS), set(range(319)))
        self.assertEqual(len(ml.KEYS), 319)
    def test_slots_and_split_parameter_address(self):
        self.assertEqual(ml.address(1, 58), (3, 64, 58, 0))
        self.assertEqual(ml.address(5, 318), (7, 64, 62, 2))
        self.assertEqual(ml.packet(33, 1, 58, 73), (0, 32, 107, 127, 66, 33, 3, 64, 58, 0, 73))
    def test_protect_factory_global_and_unknown_addresses(self):
        for slot in (-1, 0, 6, 8):
            with self.assertRaises(ValueError): ml.packet(33, slot, 58, 0)
        for param in (-1, 319, 16384):
            with self.assertRaises(ValueError): ml.packet(33, 1, param, 0)
        with self.assertRaises(ValueError): ml.packet(6, 1, 58, 0)
    def test_non_7bit_values(self):
        for value in (-1, 128, True, 1.0, '1'):
            with self.assertRaises(ValueError): ml.packet(33, 1, 58, value)
            p = preset(); p['33_58'] = value
            with self.assertRaises(ValueError): ml.validate_preset(p)
    def test_malformed_and_partial_presets(self):
        for mutation in ['device', 'version', 'missing', 'unknown']:
            p = preset()
            if mutation == 'device': p['device'] = 'KeyLab'
            elif mutation == 'version': p['version'] = '0.1'
            elif mutation == 'missing': del p['33_58']
            else: p['8_0'] = 1
            with self.assertRaises(ValueError): ml.validate_preset(p)
    def test_real_mcc_trailing_comma_and_bom(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'mcc.minilab3'
            text = json.dumps(preset(), indent=2)
            p.write_text('\ufeff' + text[:-1].rstrip() + ',\n}', encoding='utf-8')
            self.assertEqual(ml.load_preset(p), preset())
    def test_trailing_comma_filter_preserves_strings_and_escapes(self):
        text = r'{"text":",}","escape":"\\\"", "array":[1,],}'
        data = json.loads(ml.strip_trailing_commas(text))
        self.assertEqual(data['text'], ',}')
        self.assertEqual(data['array'], [1])
    def test_mcc_style_duplicate_keys_still_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.minilab3'
            p.write_text('{"device":"MiniLab 3","device":"MiniLab 3",}')
            with self.assertRaises(ValueError): ml.load_preset(p)
    def test_duplicate_json_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.json'; p.write_text('{"device":"MiniLab 3","device":"MiniLab 3"}')
            with self.assertRaises(ValueError): ml.load_preset(p)
    def test_invalid_file_never_opens_port(self):
        with tempfile.TemporaryDirectory() as d, patch.object(ml, 'MiniLab') as device:
            p = Path(d) / 'bad.json'; p.write_text('{}')
            with self.assertRaises(ValueError): ml.main(['preset', 'upload', str(p), '--slot', '1'])
            device.assert_not_called()
    def test_exclusive_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'old.json'; p.write_text('original')
            with self.assertRaises(FileExistsError): ml.save_json(p, {})
            self.assertEqual(p.read_text(), 'original')


class TransportTests(unittest.TestCase):
    def test_full_reply_address_filters_stale_and_foreign_responses(self):
        dev = ml.MiniLab.__new__(ml.MiniLab)
        queue = []
        class Input:
            def iter_pending(self):
                result = list(queue); queue.clear(); return result
        def send(message):
            wrong = ml.HEADER + (33, 4, 64, 58, 0, 12)
            correct = ml.HEADER + (33, 3, 64, 58, 0, 73)
            queue.extend([SimpleNamespace(type='sysex', data=wrong),
                          SimpleNamespace(type='sysex', data=correct)])
        dev.input = Input(); dev.output = SimpleNamespace(send=send)
        dev.mido = SimpleNamespace(Message=lambda kind, data: SimpleNamespace(type=kind, data=data))
        dev.timeout = .1
        self.assertEqual(dev.read(1, 58), 73)
    def test_identity_rejects_another_manufacturer_on_named_port(self):
        dev = ml.MiniLab.__new__(ml.MiniLab); dev.port = 'Minilab3 MIDI'
        dev.exchange = lambda *args: (126,127,6,2,0,1,2,2,0,4,4,69,0,2,1)
        with self.assertRaisesRegex(ValueError, 'identity'): dev.identify()
    def test_store_is_limited_to_user_memories(self):
        dev = ml.MiniLab.__new__(ml.MiniLab); sent = []
        dev.output = SimpleNamespace(send=lambda m: sent.append(m.data))
        dev.mido = SimpleNamespace(Message=lambda kind, data: SimpleNamespace(data=data))
        with patch.object(ml.time, 'sleep'):
            dev.store(1); dev.store(5)
            for slot in [0,6,True,1.1]:
                with self.assertRaises(ValueError): dev.store(slot)
        self.assertEqual(sent, [ml.HEADER+(6,3), ml.HEADER+(6,7)])


class MonitorTests(unittest.TestCase):
    def test_notes_and_cc_are_distinct_without_using_preset_addresses(self):
        class Message:
            def __init__(self, kind, data, raw):
                self.type = kind; self.values = {'type': kind, **data, 'time': 0}; self.raw = raw
                self.__dict__.update(data)
            def dict(self): return dict(self.values)
            def bytes(self): return self.raw
        note = Message('note_on', {'channel':15,'note':59,'velocity':80}, [159,59,80])
        event = ml.describe_message(note)
        self.assertEqual(event['type'], 'note_on'); self.assertEqual(event['note_name'], 'B3')
        self.assertEqual(event['channel'], 16); self.assertEqual(event['raw_hex'], '9F 3B 50')
        self.assertNotIn('control', event)
        cc = Message('control_change', {'channel':15,'control':1,'value':0}, [191,1,0])
        event = ml.describe_message(cc)
        self.assertEqual(event['type'], 'control_change'); self.assertEqual(event['control'], 1)
        self.assertNotIn('note_name', event)
        self.assertIsNone(ml.describe_message(Message('sysex', {}, [])))
    def test_invalid_monitor_duration_never_opens_device(self):
        with patch.object(ml, 'MiniLab') as device:
            for seconds in ('-1','0','3601','nan'):
                with self.assertRaises(ValueError): ml.main(['monitor','--seconds',seconds])
            device.assert_not_called()


class UploadTests(unittest.TestCase):
    def test_dry_run_has_no_writes_or_files(self):
        f = Fake(); p = preset(); p['33_58'] = 73
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'backup'
            r = ml.upload(f, p, 1, target, True)
            self.assertEqual(r['changes'], [('33_58', 0, 73)])
            self.assertEqual(f.writes, []); self.assertFalse(target.exists())
    def test_backup_precedes_every_write_and_only_changes_written(self):
        f = Fake(); p = preset(); p['33_58'] = 73; p['34_59'] = 75
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'backup'; original_write = f.write
            def write(*args):
                self.assertTrue((target / 'before.minilab3').is_file())
                original_write(*args)
            f.write = write
            r = ml.upload(f, p, 1, target)
            self.assertEqual(f.writes, [(1, 58, 73), (1, 59, 75)])
            self.assertEqual(r['verified_fields'], 319)
            self.assertEqual(f.stores, [1])
            self.assertEqual(ml.load_preset(target / 'after.minilab3'), p)
    def test_failed_reply_rolls_back_even_partially_accepted_write(self):
        f = Fake(fail_param=59); p = preset(); p['33_58'] = 73; p['34_59'] = 75
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'backup'
            with self.assertRaisesRegex(RuntimeError, 'restored and verified'): ml.upload(f, p, 1, target)
            self.assertEqual(f.data, preset())
            self.assertEqual(f.stores, [1])
            self.assertTrue((target / 'rollback.minilab3').exists())
            self.assertFalse((target / 'receipt.json').exists())
    def test_disconnect_keeps_backup_and_reports_incomplete_rollback(self):
        f = Fake(fail_param=58, disconnect=True); p = preset(); p['33_58'] = 73
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'backup'
            with self.assertRaisesRegex(RuntimeError, 'Rollback incomplete'): ml.upload(f, p, 1, target)
            self.assertTrue((target / 'before.minilab3').exists())
            self.assertTrue((target / 'failure.json').exists())
            self.assertFalse((target / 'receipt.json').exists())
    def test_existing_backup_directory_prevents_any_write(self):
        f = Fake(); p = preset(); p['33_58'] = 73
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError): ml.upload(f, p, 1, d)
            self.assertEqual(f.writes, [])
    def test_unknown_firmware_refused_without_writes(self):
        f = Fake(); f.identity = {'firmware': '2.0.0.0'}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'firmware'): ml.upload(f, preset(), 1, Path(d) / 'backup')
            self.assertEqual(f.writes, [])
    def test_unchanged_preset_writes_nothing_but_verifies(self):
        f = Fake()
        with tempfile.TemporaryDirectory() as d:
            r = ml.upload(f, preset(), 1, Path(d) / 'backup')
            self.assertEqual(r['changed_fields'], 0); self.assertEqual(f.writes, [])


if __name__ == '__main__': unittest.main()
