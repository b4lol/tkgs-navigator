# Architecture

```text
Enigma2 screen → tuner lock → worker (separate process)
                              ↓
demux → section framing → CRC + table collection → local parsing
                                                    ↓
layout choice (observed | fixed records, if proven) → lamedb 4/5 matching
                                                    ↓
bouquet plan (main, HD/SD, categories, radio) → JSON preview → screen
                                                    ↓ Yellow key (or opt-in auto)
captured raw record → re-validation → backup → bouquet set + indexes
```

## Modules

| Module | Responsibility |
|---|---|
| `core/constants.py` | PID, table id, orbital, TKGS transponders, service types — single source |
| `core/crc.py` | MPEG-2 CRC-32 (poly 0x04C11DB7), precomputed table |
| `core/sections.py` | Section framing, version and duplicate handling |
| `core/text.py` | Channel-name codec detection and control-character cleaning |
| `core/parser.py` | Observed TKGS service-name / LCN relationships; `Channel` |
| `core/records.py` | Research report's fixed-record layout, strictly validated |
| `core/lamedb.py` | Read-only lamedb, satellite checks, tuning candidates, full-key matching, key hit rate |
| `core/dvb.py` | Architecture-aware ioctl codes (incl. MIPS/PPC/SPARC/PA-RISC/Alpha), `struct`-packed filter, nonblocking demux, timeout/idle/cancel, CRC fallback, raw recording |
| `core/workflow.py` | Shared live/offline flow, capture and recording files, layout choice, single-pass analysis |
| `core/bouquets.py` | Bouquet plan: variant choice, alternate list, categories, radio, LCN spacers |
| `core/inspect.py` | Per-table summary of a recording under both layouts |
| `core/storage.py` | Lock, bouquet-set transaction, manifest whitelist, backup, rollback |
| `worker.py` | JSON-lines CLI (`scan`, `apply`, `restore`, `record`, `inspect`), exit codes |
| `ui/config.py` | Persistent plugin settings, worker options, demux resolution |
| `ui/checks.py` | Recording, 42.0°E tuner and transponder checks; channel-search offer |
| `ui/controller.py` | Scan state machine: tuning, fallback, worker, playback restore |
| `ui/auto.py` | Opt-in daily update in standby |
| `ui/i18n.py` | gettext domain with fallback to the enigma2 catalog |
| `ui/signals.py` | Bridge the two Enigma2 signal styles |
| `ui/skin.py` | Resolution-scaled skin for HD/SD desktops |
| `ui/screen.py` | Settings, preview and results on top of the controller |

The `core` package has no Enigma2 imports and is fully testable off-device. The `ui`
package is imported only on the receiver. The worker is launched as a separate process
so parsing never blocks the GUI event loop.

## Transponder fallback

The controller builds its candidate list from the configured transponder followed by
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

## Evidence-gated layout

`core/records.py` implements the September 2026 research report's record layout
(u16 SID, TSID, ONID, LCN; u8 flags, name length; name; u8 package). The report marks it
as unverified, and its examples are inconsistent (ONID 1070 vs 0x9E; 0x5A0000 given as
the 42.0°E namespace, which is 9.0°E — 42.0°E is 0x01A40000). It is therefore trusted
only when:

1. every section splits into whole records with no byte left over, no unknown flag bit,
   a non-zero SID, an LCN in 1..9999 and a decodable name, and
2. at least half of the (ONID, TSID, SID) keys exist on 42.0°E in lamedb.

Otherwise the observed layout is used, exactly as before. The observed fixtures and a
random corpus never pass the first test. Matching against lamedb by full key doubles
as spoofing protection, so no hard-coded ONID filter is needed. The probe costs about
0.25 ms against 5.7 ms for the observed parse on the 500-channel benchmark.

Only the record layout carries HD, radio and package fields, so the alternate HD/SD list,
the radio bouquet and category bouquets appear only with it; package names are the
report's and provisional. `worker record` and `worker inspect` collect and summarise a
real recording so the layout, subtables (HD and SD lists may be separate
`table_id_extension`s) and package ids can be confirmed or corrected.

## Bouquet set and backups

A plan lists every bouquet the plugin should own. The store compares it with the
`userbouquet.tkgs_navigator*` files present, writes new and changed bouquets, then the
two indexes (links replaced in place, optionally at the top), then removes bouquets no
longer planned, and rolls back in reverse on failure. Manifests (schema 2) record the
digest of every touched file before and after; restore accepts only the plugin's bouquet
names and the two indexes, and still reads schema 1 manifests from 0.3.0.

*Channel number = LCN* inserts `1:832:D:0:0:0:0:0:0:0:` spacers (invisible numbered
markers, as used by AutoBouquetsMaker) for gaps up to LCN 2000.

## Automation safeguards

The background updater never runs unless enabled, runs only in standby with no recording
running or due within 30 minutes, and applies only with a second opt-in and a clean
preview. It shares the controller with the screen, so tuning, fallback and playback
handling are the same; leaving standby hands playback back to Enigma2 and cancels the run.

The plugin never starts the receiver's channel search by itself: it asks first, and the
background updater only skips when transponders are missing. Tuner choice is left to
Enigma2's own allocation; the plugin checks that some DVB-S tuner is set up for 42.0°E
and reads the demux of the locked service.

## Still out of scope

- Python 2.7: the code relies on Python 3 (postponed annotations, dataclasses, pathlib,
  `os.replace`), which the report's own typing and f-string rules also require.
- In-process capture with `eDVBSectionReader`: its availability differs between images,
  and the separate worker already keeps the GUI responsive while isolating parse errors.
