# Validation

**103 tests pass** in the development environment, on Python 3.14.7 and on Python
3.8.20. Coverage: the CRC known vector, corrupt and partial sections, the CRC fallback
retry and its propagation to preview and capture files, version transitions, a
512-entry deterministic malformed-data corpus, lamedb 4/5, SID ambiguities, full-key and
radio matching, the fixed-record layout (strict rejection, random corpus, HD/SD pairs,
choice by lamedb hit rate), raw recording and subtable selection, the inspection
report, bouquet plans (variants, categories, radio, LCN spacers), multi-file apply,
removal of stale bouquets, rollback on an index failure, schema 1 restore and rejection
of manifests naming foreign files, transponder candidates and fallback on no lock or no
data, demux detection, the 42.0°E tuner check and channel-search offer, the automatic
update rules (hour, standby, recordings, opt-in apply, leaving standby), CLI
preview/apply/restore/record/inspect, the mock Enigma2 playback and cancel lifecycle,
translation lookup and template sync, DEB/IPK reproducibility and preservation of source
files in the package. `ruff` and `mypy --strict` (core and worker) pass. The CI matrix
covers 3.8, 3.10, 3.12 and 3.14.

Development measurement of 24 September 2026, Python 3.14.7 / x86_64:

| Metric | Result |
|---|---:|
| Synthetic channel count | 500 |
| Sections / total bytes | 10 / 17012 |
| Median of 30 parse runs | 5.936 ms |
| 95th-percentile parse time | 6.440 ms |
| Unique sections kept for 200 inputs | 10 |
| Duplicates dropped | 190 |
| Record-layout probe on the same data | 0.249 ms |

This result is not a comparative speed-up claim against the old project.

Test and measurement results are produced in the development environment. Synthetic
data does not mean testing was done on a real Türksat broadcast and Enigma2 device.

```sh
python3 -m unittest discover -v
python3 tools/benchmark.py
python3 tools/build.py
```

`benchmark.json` records the parse time for 500 synthetic channels and the number of
sections the collector keeps in the face of duplicates. `tracemalloc` measures only
traced Python allocations; it is not process RSS or all capture buffers.

Package generation uses the Python standard library. With the same files and the same
`SOURCE_DATE_EPOCH`, the same byte sequence is produced in the same environment. The
gzip header is written explicitly (fixed MTIME, XFL and OS bytes), so it does not
depend on the platform or Python version; the compressed body still comes from the
system zlib, so byte-identical checksums are guaranteed for a given zlib release.

To be verified additionally on a real device: HD/SD screen appearance, key mapping,
tuner lock, the demux reported by the locked service, a complete live TKGS table, channel
matches, playback after cancellation, the refresh of the Enigma2 bouquet cache, channel
numbers with LCN spacers, and a daily update in standby (including waking the receiver
during it). A `worker record` / `worker inspect` run on the live transponder decides
whether the fixed-record layout, its HD/SD and radio flags and the package ids hold.