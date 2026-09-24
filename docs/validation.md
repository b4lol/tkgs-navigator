# Validation

**49 tests pass** in the development environment. Coverage: the CRC known vector,
corrupt and partial sections, the CRC fallback retry, version transitions, a
512-entry deterministic
malformed-data corpus, lamedb 4/5, SID ambiguities, rollback on a write failure,
backup integrity, CLI preview/apply/restore, the mock Enigma2 playback and cancel
lifecycle, DEB/IPK reproducibility and preservation of source files in the package.
Python 3.8 syntax is also verified; the version running the tests here is 3.14.7.
A CI matrix is prepared for other Python versions but was not run here.

Development measurement of 24 September 2026, Python 3.14.7 / x86_64:

| Metric | Result |
|---|---:|
| Synthetic channel count | 500 |
| Sections / total bytes | 10 / 17012 |
| Median of 30 parse runs | 5.485 ms |
| 95th-percentile parse time | 7.924 ms |
| Unique sections kept for 200 inputs | 10 |
| Duplicates dropped | 190 |

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
tuner lock, the selected demux path, a complete live TKGS table, channel matches,
playback after cancellation and the refresh of the Enigma2 bouquet cache.
