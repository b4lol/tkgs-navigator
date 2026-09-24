# TKGS Navigator

A local TKGS scanning and channel-ordering plugin for Enigma2 images running
**Python 3.8 or newer**. It follows a scan → preview → apply flow. No server,
WebIf, API key or third-party Python package is required. Version:
**0.4.0 — pending hardware validation**.

## What it does

- Tunes to the TKGS frequency on Türksat 42°E and waits for tuner lock. It tries the
  configured transponder first, then the known TKGS data transponders (12380 V 27500,
  12423 H 30000) that exist in the receiver's lamedb, moving on when the tuner does not
  lock or when no TKGS data arrives within 20 seconds.
- Reads PID 8181 / table 0xA7 sections in a separate process without blocking the UI.
- Checks CRC, table version and section completeness; duplicates are not stored.
  If the table is still incomplete after 25 seconds, the hardware CRC check is
  switched off and capturing continues; structural and version checks still apply.
  Such a preview carries a warning and the capture file records the CRC mode.
- Stops as soon as the table is complete instead of waiting out the timeout.
  On error or cancellation it returns to the previous channel.
- Resolves channel names and LCN order locally, matching against lamedb 4/5. The
  fixed-record layout proposed by the 2026 research report (SID, TSID, ONID, LCN, flags,
  name, package) is used only when the table parses into it without a single stray byte
  and at least half of its keys exist in lamedb; otherwise the layout observed in the
  reference is used. The preview names the layout it chose and why.
- Shows the results first. The Yellow key builds a separate **TKGS Navigator** bouquet.
  When the table carries the fields, it also builds an alternate HD or SD list, a radio
  bouquet and, if enabled, one bouquet per package.
- Optionally pads the main bouquet with invisible numbered markers so channel numbers
  equal LCNs, and can link the bouquets at the top of the bouquet list.
- Backs up before every change. The Blue key undoes the last change.
- Optionally updates once a day in standby; applying the result automatically is a
  separate opt-in (see *Automatic update*).
- UI texts are English and translatable with gettext (`locale/TKGSNavigator.pot`).

`lamedb`, the old TKGS plugin and other bouquet contents are left untouched. It does
not create new services for unknown frequencies; the receiver's built-in network scan
must be run first; when no TKGS transponder is known, the plugin offers to open the
receiver's channel search. By default it orders by LCN without gaps, so the number on
the remote is not always the broadcaster's LCN; enable *Channel number = LCN* for that.
Numbers then match only if this bouquet is numbered from 1, i.e. it is the first
bouquet (*Bouquet at the top*) or the image numbers each bouquet separately.

## Installation

Prebuilt packages are in `dist/`. Copy the one that fits your device:

```sh
# opkg images such as OpenATV / OpenPLi
opkg install /tmp/enigma2-plugin-extensions-tkgs-navigator_0.4.0_all.ipk

# Python 3 images using dpkg
dpkg -i /tmp/enigma2-plugin-extensions-tkgs-navigator_0.4.0_all.deb
```

Then restart the Enigma2 GUI and open **Plugins → TKGS Navigator**. No install script
is run; the Python sources stay readable inside the package. The IPK declares a
`python3-modules` dependency to provide the standard-library modules.

The default install path is `/usr/lib/enigma2/python/Plugins/Extensions/TKGSNavigator`.
For an image that looks for plugins under `/usr/lib64`, build the package with
`python3 tools/build.py --libdir usr/lib64 --output dist-lib64`. Python 2 is not
supported; actual compatibility on DreamOS/OpenATV/OpenPLi has not been verified yet.

## Usage

1. In the receiver's channel search, scan the TKGS frequency with network search on.
2. In the plugin, set the frequency, polarization and symbol rate. The starting values
   come from the reference package: **12380 V 27500**; currency is not guaranteed. If this
   transponder is missing or silent, the known TKGS transponders are tried automatically.
3. *Demux selection* is automatic: the demux of the locked TKGS service is used when
   the image reports it. Otherwise, or in manual mode, the configured adapter and demux
   (`adapter0/demux0` by default) are used.
4. **Green / OK:** scan. **Red:** cancel while scanning, close when idle.
5. Review the results. **Yellow:** apply the list. **Blue:** undo the last change.
   Up/Down selects the settings row; Left/Right and the digits edit it. The page keys
   scroll the result list; the exact key mapping depends on the image keymap.

Bouquet settings: *Main list* (HD or SD variant where a table offers both), *Category
bouquets*, *Channel number = LCN* and *Bouquet at the top*. They apply to the preview
and to the list written from it.

A scan is not started while a recording is active or when no DVB-S tuner is set up for
42.0°E. Recording or PiP tuner routing is
not managed in this release. An incomplete or conflicting table cannot be applied. If
the same SID appears in more than one Türksat service, no channel is chosen at random;
the match is skipped.

## Automatic update

*Daily automatic update* is off by default. When on, the plugin checks every five
minutes whether the chosen *Update hour* has passed since the last run; it then scans
only if the receiver is in standby and no recording is running or starts within 30
minutes. Leaving standby cancels the scan and it is retried at the next standby.

The result is written only when *Apply updates automatically* is also on and the
preview is complete, applicable and has no warnings. Otherwise nothing changes and the
next manual scan shows the current table. Every automatic apply is backed up like a
manual one and can be undone with the Blue key.

## Studying the live table

Category and HD/SD bouquets depend on how the broadcast table is laid out, which has not
been confirmed against a recording. On the receiver, tuned to the TKGS transponder
(for example by starting a scan in the plugin and cancelling it after the lock), record
the raw sections and summarise them:

```sh
cd /usr/lib/enigma2/python/Plugins/Extensions
python3 TKGSNavigator/worker.py record --device /dev/dvb/adapter0/demux0 \
  --seconds 120 --all-tables --output /tmp/tkgs-raw.json
python3 TKGSNavigator/worker.py inspect --capture /tmp/tkgs-raw.json \
  --lamedb /etc/enigma2/lamedb
```

`record` keeps every distinct section on PID 8181 for the given time (all subtables and
versions, at most 4096 sections / 8 MiB, hardware CRC off); `--all-tables` also keeps
table ids other than 0xA7. `inspect` reports per table the versions, completeness, CRC
results, the first bytes in hex, what each layout makes of the data (flag, package and
ONID counts, samples) and how many record keys lamedb knows. `scan` and `apply` accept
the recording directly and use its best subtable, or the one given with `--extension`.

## Running on a computer

Preview with a synthetic capture, without Enigma2 or DVB hardware:

```sh
python3 -m TKGSNavigator.worker scan \
  --capture examples/synthetic-capture.json --lamedb examples/lamedb
python3 -m unittest discover -v
python3 tools/benchmark.py
python3 tools/build.py
```

Development checks, as run in CI: `ruff check .`, `ruff format --check .` and `mypy`
(strict, for `core/` and `worker.py`).

The commands print JSON lines. `scan` never writes to channel files. Exit codes:
`0` success, `1` error, `2` preview cannot be applied, `130` cancelled.

To capture from a demux already tuned to the TKGS frequency on the device:

```sh
python3 -m TKGSNavigator.worker scan --device /dev/dvb/adapter0/demux0 \
  --lamedb /etc/enigma2/lamedb --timeout 60 --idle-timeout 20 --save-capture /tmp/tkgs-capture.json
```

Because the package is installed under `Plugins.Extensions`, run these `-m` examples
from the project root, or use the full path to the installed `worker.py`.

Applying a capture explicitly and restoring a backup:

```sh
python3 -m TKGSNavigator.worker apply --capture /tmp/tkgs-capture.json --config-dir /etc/enigma2
python3 -m TKGSNavigator.worker restore --config-dir /etc/enigma2 --backup BACKUP_ID
```

The CLI does not change playback and does not reload Enigma2 bouquets; the plugin UI
does that. `apply` re-matches against the current service database.

## Data integrity

Files are updated with a temporary file + `fsync` + atomic `replace`. An update writes
the plugin's bouquets (`userbouquet.tkgs_navigator*.tv` / `.radio`) and its links in
`bouquets.tv` and `bouquets.radio` as one set: bouquets are written before the links and
bouquets no longer planned are removed after them. For each operation an immutable
backup and a SHA-256 manifest are kept under `/etc/enigma2/tkgs-navigator-backups/`; if
any file fails, the files already changed are rolled back. Only the plugin's own bouquet
names and the two index files can appear in a manifest, so a damaged or edited backup
cannot write anywhere else. Backups made by 0.3.0 can still be restored.

A lock prevents concurrent writes by the same plugin; other channel editors do not share
this lock. Do not apply a list while Enigma2 or another plugin is writing.

The set is not one atomic transaction. On sudden power loss part of it may be updated;
the backup is preserved and automatic rollback never writes to mismatched files. In
that case, inspect the manifest and restore the backup by hand. Backups are not
deleted automatically.

## Scope and validation

No complete official TKGS specification or current broadcast recording is available.
The parser uses the layout observed in the reference, or the research report's record
layout when the data proves it; other layouts may be unsupported. Package names for
category bouquets come from the report and are provisional until a recording confirms
the ids. `record` and `inspect` exist to settle both questions on a real receiver. Tests run against synthetic sections, corrupt data, file errors and
mock Enigma2/DVB APIs. It should not be considered verified on a production device until
picture, tuner routing and broadcast decoding are checked on a real satellite receiver.

## Translations

Compiled catalogs go to `TKGSNavigator/locale/<lang>/LC_MESSAGES/TKGSNavigator.mo` and
are packaged automatically. Words the plugin does not translate fall back to the
image's own enigma2 catalog. After changing UI texts, regenerate the template:

```sh
xgettext --language=Python --keyword=_ --from-code=UTF-8 --sort-by-file \
  -o TKGSNavigator/locale/TKGSNavigator.pot TKGSNavigator/plugin.py TKGSNavigator/ui/*.py
```

## License

GPL-2.0-or-later. The full text is in [LICENSE](LICENSE) and is included in the package.
The application code was rewritten for this project; see [NOTICE.md](NOTICE.md) for details.

Architecture: [docs/architecture.md](docs/architecture.md)
Source review note: [NOTICE.md](NOTICE.md)
Measurements: [docs/validation.md](docs/validation.md)
