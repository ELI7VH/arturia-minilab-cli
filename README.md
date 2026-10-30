# Arturia MiniLab CLI

Upload a native `.minilab3` mapping to a User slot without opening MIDI Control
Center. This programs controller presets; it does not update device firmware.

## Install

Requires Python 3, USB MIDI access and a MiniLab 3. Tested on macOS with controller
firmware **1.2.0.69**. Other firmware can be identified/read, but uploads are refused
until validated. The supported preset format is **1.2.0**, with all 319 fields. Native MCC exports
with trailing commas and UTF-8 BOMs are accepted; duplicate keys remain refused.

```sh
git clone https://github.com/ELI7VH/arturia-minilab-cli.git
cd arturia-minilab-cli
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./minilab list
```

Optional short command (choose a directory on your PATH; do not replace another tool):

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/minilab" ~/.local/bin/minilab
```

## Use

Close MIDI Control Center while uploading. Choose the intended **User slot 1–5**.
`--slot 1` means the physical **User 1** program, not Arturia/DAW memory.

```sh
minilab list
minilab monitor --seconds 30
minilab backup --slot 1 --out ~/Desktop/user1-before.minilab3
minilab diff ~/repos/switchblade/controllers/fm1-sb-live.minilab3 --slot 1
minilab preset upload ~/repos/switchblade/controllers/fm1-sb-live.minilab3 --slot 1 --dry-run
minilab preset upload ~/repos/switchblade/controllers/fm1-sb-live.minilab3 --slot 1
```

`monitor` displays actual incoming notes (with note names), CCs and raw bytes.
Use it to verify physical controls independently of configuration readback.
For example, piano B is a `note_on`; CC1 is a separate `control_change`.
Preset uploads do not set the global keyboard channel. For WaveLoop Switch Blade,
set the keyboard to channel 16 to follow the selected track; channel 1 plays track 1.

After upload, switch to another program, then select the written User program
on the MiniLab (Shift + Prog if needed). This reloads the stored mapping into the
active controls. Slot readback does not prove that the active program uses it;
the receipt reports `active_program_verified: false` and gives the reload step.
Then use `minilab monitor` while moving the physical controls.
The tool preserves its existing name. Native `.minilab3` mapping files do not
contain global keyboard settings or preset names, so those are not part of export.

Every upload saves the original mapping, requested mapping, device identity,
change plan and hashes in a fresh folder under `~/Documents/MiniLab Backups/`.
By default only changed fields are written. Add `--rewrite` to reapply all 319
fields even when the mapping already matches. It uses the same backup, rollback
and full verification safeguards. The preset-store command follows, then all
319 fields are read back. The receipt is written only on success. Existing
recovery files are never overwritten. Use `--backup-dir /new/folder` to choose
another recovery destination.

On failure the tool attempts to restore the changed fields and store/read back
the original mapping. If USB disconnects, it reports incomplete recovery and
keeps the original file. Restore with the same upload command:

```sh
minilab preset upload '/path/to/backup/before.minilab3' --slot 1
```

Arturia and DAW factory slots, global parameters and firmware commands are
excluded. Input validation checks the complete native field set and 7-bit values;
it does not implement every MCC field's semantic range rules. Use a known-good
MCC/WaveLoop preset. Unsupported JSON keys/versions are refused before MIDI opens.
No automatic selection, preset renaming, firmware flashing or MIDI routing daemon.

## Verification

```sh
python3 -m unittest discover -s . -p 'test_*.py' -v
```

2026-10-29: identified the connected MiniLab 3 on `Minilab3 MIDI`, firmware 1.2.0.69.
Read all five User slots, 319 fields each, into independent private backups.
User 1 matched the old fm1-sb-live mapping exactly except its four new ADSR CCs.
CLI upload changed only CC74/71/16/17 to CC73/75/79/72 and verified all 319 fields.
Other four slots compared unchanged. Backup/restore/upload round trip and the
preset-store command were exercised on User 1. The CLI leaves User 1 with ADSR
plus the original four master FX. After Elijah unplugged/reconnected the MiniLab, identity and all 319 User 1 fields
were read again and matched the requested ADSR preset exactly (zero differences).
Restart persistence is verified. Actual physical knob-emitted MIDI remains a
separate check. The FM-1 firmware and songs were not touched.

## Source history

Originally developed and hardware-tested in
[ELI7VH/lucian-utils](https://github.com/ELI7VH/lucian-utils/tree/c6974126850967a3c89056cc300373c80715980c/controller/minilab3).
This repository is now the standalone home for the CLI.

## Protocol and credits

New Python implementation by Elijah Lucian / Lucian Labs. GPL-3.0-or-later;
see LICENSE. Protocol reference and attribution:

- [soyersoyer/sysex-controls](https://github.com/soyersoyer/sysex-controls/tree/8c090482e3b685d758056c053d3969fabee5d129),
  GPL3, especially `src/sc-midi.c` and `src/minilab3/`. This existing Linux editor
  established MiniLab3 v3 control addresses and Arturia preset-store command.
- Native JSON key layout comes from WaveLoop's existing
  `switchblade/controllers/fm1-sb-live.minilab3`; `preset-keys.json` contains only
  its field names, without any user configuration values.

SysEx payload (excluding F0/F7): manufacturer prefix `00 20 6B 7F 42`, then
read20/write21, User slot +2, page40, parameter low7, parameter high bits, and a
value for writes. Store uses the same prefix followed by06 and User slot +2.
Replies must match the full requested address; unrelated/stale MIDI is ignored.
Parameter319+ and pages outside this preset address space are refused. No
proprietary Arturia application or resource files are distributed.

Independent audit, 2026-10-29: Arturia MIDI Control Center recalled User 1 into
its own native template; all 319 fields matched the requested preset, including
exactly the four intended ADSR CC changes from the original mapping. This checks
the CLI address interpretation independently. Actual A/B key behavior remains a
separate raw-MIDI diagnosis. The importer now accepts MCC’s trailing-comma format.


Live audit, 2026-10-30: backed up all five User slots, then reapplied all 319
User 1 fields using `--rewrite`, stored and verified. The other slots and global
keyboard channel remained unchanged. Before the program reload, real knob output
used a different CC set; after reselecting User 1, the requested eight knob CCs
and four fader channels were observed on USB and at the FM-1 DIN input. The
isolated K6 test produced 121 identical Ch16 CC21 messages on USB and DIN, with
no gaps or other messages. The broader capture had one observer ring-read gap
during a fast fader burst; it verifies every fader's CC/channel identity, not
byte-complete DIN delivery for that burst. A/B pitches were captured as notes.
No synthetic knob events were used to claim physical verification.
