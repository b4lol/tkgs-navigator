# Architecture

```text
Enigma2 screen → tuner lock → worker (separate process)
                              ↓
demux → section framing → CRC + table collection → local parsing
                                                    ↓
lamedb 4/5 → SID + satellite matching → JSON preview → screen
                                                    ↓ Yellow key
captured raw record → re-validation → backup → bouquet + index
```

## Modules

| Module | Responsibility |
|---|---|
| `core/constants.py` | PID, table id, orbital, TKGS transponders, service types — single source |
| `core/crc.py` | MPEG-2 CRC-32 (poly 0x04C11DB7), precomputed table |
| `core/sections.py` | Section framing, version and duplicate handling |
| `core/text.py` | Channel-name codec detection and control-character cleaning |
| `core/parser.py` | Observed TKGS service-name / LCN relationships |
| `core/lamedb.py` | Read-only lamedb, numeric frequency and satellite checks, tuning candidates |
| `core/dvb.py` | Architecture-aware ioctl codes (incl. MIPS/PPC/SPARC/PA-RISC/Alpha), `struct`-packed filter, nonblocking demux, timeout/idle/cancel, CRC fallback |
| `core/workflow.py` | Shared live/offline flow, bounded capture file, single-pass analysis |
| `core/storage.py` | Lock, backup, atomic file write, rollback |
| `worker.py` | JSON-lines CLI, error and cancel exit codes |
| `ui/config.py` | Persistent plugin settings |
| `ui/i18n.py` | gettext domain with fallback to the enigma2 catalog |
| `ui/signals.py` | Bridge the two Enigma2 signal styles |
| `ui/skin.py` | Resolution-scaled skin for HD/SD desktops |
| `ui/screen.py` | Enigma2 event loop, transponder fallback, previous-channel restore |

The `core` package has no Enigma2 imports and is fully testable off-device. The `ui`
package is imported only on the receiver. The worker is launched as a separate process
so parsing never blocks the GUI event loop.

## Transponder fallback

The screen builds its candidate list from the configured transponder followed by
`TKGS_TRANSPONDERS`, keeping only those present in lamedb. A candidate is abandoned
when the tuner does not lock within 12 s, or when the worker's `--idle-timeout`
(20 s, capped at the scan timeout) passes without any PID 8181 data; the next
candidate is then tuned without restoring playback in between. Only when the list is
exhausted is the previous channel restored and the result shown.

## CRC fallback

If the table is still incomplete after 25 s, the demux filter is restarted without
hardware CRC checking and the collector stops verifying CRC; header, length, version
and completeness checks remain. The mode is carried through the parser, stored as
`"crc": false` in the capture file and reported as `crc_checked`, with a warning in the
preview. A table that completes is never switched to this mode.

## Performance choices

- Sections are deduplicated per `(table_id_extension, version, section_number)`.
  At most 256 sections are kept for a table; duplicates do not grow memory.
- Parsing and matching run once when the scan finishes; old data is not rescanned on
  every new packet, and `apply` reuses the same analysis instead of parsing twice.
  Tag lookups use `bytes.find`; matching uses a SID dictionary.
- CRC uses a precomputed 256-entry table.
- Progress output is sent at most twice per second; the GUI refreshes the channel
  list in one pass.
- A `select` wait is at most 200 ms with at most 32 reads per turn, so a continuous
  data stream never starves the timeout/cancel checks.
- No network request and no dependent server. The CLI/core use only the standard library.

## Known limits

The first valid `table_id_extension` is followed; there is no merging of multiple
subtables. When the version changes, earlier sections are dropped and stale-version
interleaving is rejected. If the table is still incomplete after 25 seconds, the
demux CRC check is switched off and capturing continues with the structural and
version checks still in place. Service/LCN patterns are
searched with bounds and conflict checks; the meaning of all proprietary TKGS
container fields is not known. For that reason, regression tests against a real
broadcast recording are the next validation step.

Because LCN records do not carry TSID/ONID, SID matching is narrowed to the target
satellite; if there is more than one candidate, no automatic choice is made. For
missing services, `lamedb` is not modified and the user is directed to the receiver's
network scan.

## Deliberately not implemented yet

These items from the September 2026 research report wait for a real TS capture of
PID 8181 or for hardware validation:

- A fixed record layout (SID/TSID/ONID/LCN/flags/name/package id). The report marks it
  as unverified and it differs from the layout observed in the reference, so HD/SD
  lists, category bouquets, `(ONID, TSID, SID)` matching and an ONID filter would rest
  on guessed fields. The report's own examples also disagree on the ONID (1070 vs
  0x9E) and give 0x5A0000 as the 42.0°E namespace, which is 9.0°E (42.0°E is
  0x01A40000).
- Automatic apply, daily cron updates, triggering the receiver's network scan and
  automatic NIM selection. They change the receiver without the user's review and
  cannot be verified off-device.
- Filling empty LCN slots: the table carries no channels without an LCN to place there.
- In-process capture with `eDVBSectionReader` and Python 2.7 support: the separate
  worker already keeps the GUI responsive, and Python 2 is outside the supported range.
