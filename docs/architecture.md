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
| `core/crc.py` | MPEG-2 CRC-32 (poly 0x04C11DB7), precomputed table |
| `core/sections.py` | Section framing, version and duplicate handling |
| `core/text.py` | Channel-name codec detection and control-character cleaning |
| `core/parser.py` | Observed TKGS service-name / LCN relationships |
| `core/lamedb.py` | Read-only lamedb, numeric frequency and satellite checks |
| `core/dvb.py` | ARM/x86 and MIPS ioctl codes, nonblocking demux, timeout/cancel |
| `core/workflow.py` | Shared live/offline flow, bounded capture file, single-pass analysis |
| `core/storage.py` | Lock, backup, atomic file write, rollback |
| `worker.py` | JSON-lines CLI, error and cancel exit codes |
| `ui/config.py` | Persistent plugin settings |
| `ui/signals.py` | Bridge the two Enigma2 signal styles |
| `ui/skin.py` | Resolution-scaled skin for HD/SD desktops |
| `ui/screen.py` | Enigma2 event loop, tuner and previous-channel restore |

The `core` package has no Enigma2 imports and is fully testable off-device. The `ui`
package is imported only on the receiver. The worker is launched as a separate process
so parsing never blocks the GUI event loop.

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
interleaving is rejected. CRC checking is never disabled. Service/LCN patterns are
searched with bounds and conflict checks; the meaning of all proprietary TKGS
container fields is not known. For that reason, regression tests against a real
broadcast recording are the next validation step.

Because LCN records do not carry TSID/ONID, SID matching is narrowed to the target
satellite; if there is more than one candidate, no automatic choice is made. For
missing services, `lamedb` is not modified and the user is directed to the receiver's
network scan.
