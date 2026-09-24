# TKGS Navigator

A local TKGS scanning and channel-ordering plugin for Enigma2 images running
**Python 3.8 or newer**. It follows a scan → preview → apply flow. No server,
WebIf, API key or third-party Python package is required. Version:
**0.3.0 — pending hardware validation**.

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
- Resolves channel names and LCN order locally, matching against lamedb 4/5.
- Shows the results first. The Yellow key builds a separate **TKGS Navigator** bouquet.
- Backs up before every change. The Blue key undoes the last change.
- UI texts are English and translatable with gettext (`locale/TKGSNavigator.pot`).

`lamedb`, the old TKGS plugin and other bouquet contents are left untouched. It does
not create new services for unknown frequencies; the receiver's built-in network scan
must be run first. It orders by LCN and does not insert placeholder services for empty
LCN slots, so the number entered on the remote is not always the broadcaster's LCN.

## Installation

Prebuilt packages are in `dist/`. Copy the one that fits your device:

```sh
# opkg images such as OpenATV / OpenPLi
opkg install /tmp/enigma2-plugin-extensions-tkgs-navigator_0.3.0_all.ipk

# Python 3 images using dpkg
dpkg -i /tmp/enigma2-plugin-extensions-tkgs-navigator_0.3.0_all.deb
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
3. Choose the DVB adapter/demux values for the tuner path in use. `adapter0/demux0`
   is the default. There is no automatic path detection on multi-tuner devices.
4. **Green / OK:** scan. **Red:** cancel while scanning, close when idle.
5. Review the results. **Yellow:** apply the list. **Blue:** undo the last change.
   Up/Down selects the settings row; Left/Right and the digits edit it. The page keys
   scroll the result list; the exact key mapping depends on the image keymap.

A scan is not started while a recording is active. Recording or PiP tuner routing is
not managed in this release. An incomplete or conflicting table cannot be applied. If
the same SID appears in more than one Türksat service, no channel is chosen at random;
the match is skipped.

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

Files are updated with a temporary file + `fsync` + atomic `replace`. For each
operation an immutable backup and a SHA-256 manifest are kept under
`/etc/enigma2/tkgs-navigator-backups/`. If the second file fails, the first change is
rolled back. A lock prevents concurrent writes by the same plugin; other channel
editors do not share this lock. Do not apply a list while Enigma2 or another plugin
is writing.

The two files are not one atomic transaction. On sudden power loss one may be updated;
the backup is preserved and automatic rollback never writes to mismatched files. In
that case, inspect the manifest and restore the backup by hand. Backups are not
deleted automatically.

## Scope and validation

The local parser applies the reference's observed byte layout; no complete official
TKGS specification or current broadcast recording is available. Other broadcast layouts
may be unsupported. Tests run against synthetic sections, corrupt data, file errors and
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
