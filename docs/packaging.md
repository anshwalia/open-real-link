# OpenRealLink — packaging and releases

Status: **macOS bundling implemented and verified; the Windows transport is
written but no bundle is built.** Linux has no live transport yet, so there is
nothing to ship there. This document covers what exists, how to build it, and
what a public release still needs.

---

## 1. What the bundle is

A macOS **`OpenRealLink.app`**, frozen with [PyInstaller](https://pyinstaller.org)
from the repository checkout. It bundles the interpreter, PySide6, the pyobjc
`IOBluetooth` framework, `tlcore`, `tltransport`, `tlapp` and the replay
fixtures, so the app runs on a Mac with **no Python and no pip**.

The build is described by these files:

| File | Role |
| --- | --- |
| `release/macos/open-real-link.spec` | the PyInstaller recipe: entry point, hidden imports, bundled data, `Info.plist` |
| `release/macos/build.sh` | the wrapper: sets up the venv, runs PyInstaller, optionally makes a `.dmg` |
| `release/macos/entry.py` | the frozen entry point; calls `tlapp.app.main` |
| `release/macos/make-icon.sh` | turns `icon.svg` / `icon.png` into the `.icns` the bundle needs |

The bundle identifier is `io.github.anshwalia.openreallink` and the version is
read from `pyproject.toml`, so the bundle cannot drift from the package version.

## 2. Building

From the repository root:

```console
$ release/macos/build.sh
```

The script uses the repo virtualenv (`./.venv`) unless `PYTHON` is set, installs
the `[gui,macos,release]` extras, and writes the result to `dist/macos/`. To also
wrap the app in a compressed disk image:

```console
$ MAKE_DMG=1 release/macos/build.sh
```

Output:

| Path | What |
| --- | --- |
| `dist/macos/OpenRealLink.app` | the app bundle (~90 MB) |
| `dist/macos/OpenRealLink.dmg` | the disk image (~37 MB), with `MAKE_DMG=1` |

`dist/` and `build/` are ignored by git; nothing in them is committed.

### What is inside

* **`Info.plist`** carries `NSBluetoothAlwaysUsageDescription` and
  `NSBluetoothPeripheralUsageDescription`. macOS 11+ **terminates** an app that
  touches Bluetooth without a usage description, so these are not optional.
* **Replay fixtures** are bundled under `Contents/Resources/fixtures`. PyInstaller
  symlinks that into `Contents/Frameworks/fixtures`, which is where
  `tltransport.replay.FIXTURES_DIR` looks inside a frozen app — so
  `--replay <fixture>` keeps working from the bundle, with no device.
* **The signature is ad-hoc** by default. That is enough to run the app on the
  machine that built it, and nothing more.

## 3. Running the built app

```console
$ open dist/macos/OpenRealLink.app
$ dist/macos/OpenRealLink.app/Contents/MacOS/OpenRealLink --replay spp-smoketest.jsonl
```

The command-line arguments are the same as `python -m tlapp` (`--address`,
`--connect`, `--replay`, …).

## 4. Signing and notarisation

An ad-hoc bundle is **rejected by Gatekeeper** on any other Mac ("OpenRealLink
can't be opened because Apple cannot check it for malicious software"). Shipping
a public download needs a paid Apple Developer account and two steps:

1. **Sign with a Developer ID**, with the hardened runtime:
   ```console
   $ codesign --force --deep --options runtime --timestamp \
       --sign "Developer ID Application: <NAME> (<TEAMID>)" \
       dist/macos/OpenRealLink.app
   ```
   PyInstaller can do this during the build: set `codesign_identity` in the
   `EXE`/`BUNDLE` call in the spec (and an `entitlements_file` if the hardened
   runtime needs one).
2. **Notarise and staple**:
   ```console
   $ ditto -c -k --keepParent dist/macos/OpenRealLink.app OpenRealLink.zip
   $ xcrun notarytool submit OpenRealLink.zip --keychain-profile <PROFILE> --wait
   $ xcrun stapler staple dist/macos/OpenRealLink.app
   ```

The hardened runtime may need the
`com.apple.security.cs.allow-unsigned-executable-memory` and
`com.apple.security.cs.disable-library-validation` entitlements for a frozen
Python app; add them to an `entitlements.plist` and pass it via the spec when you
sign for real.

### Universal binaries

The build produces a **thin** binary for the machine it runs on (arm64 here).
A universal2 bundle needs a universal2 Python and universal2 PySide6/pyobjc, and
`target_arch="universal2"` in the spec. Not attempted yet.

### App icon

macOS app icons must be `.icns`; an SVG or PNG cannot be used directly. Drop a
source next to the spec and let the helper build it:

```console
$ release/macos/make-icon.sh                 # icon.svg / icon.png -> icon.icns
$ release/macos/make-icon.sh logo.svg         # explicit source
```

The script rasterises the source to 1024×1024 with the system `sips` (which reads
SVG on recent macOS), writes every size macOS expects, and compiles them with
`iconutil`. If `sips` cannot read the source it falls back to
`rsvg-convert` / `cairosvg` / `inkscape`. `build.sh` calls it automatically when
an `icon.svg` or `icon.png` is present, and the spec uses `icon.icns` if it
exists. Without one the bundle gets the default PyInstaller icon.

macOS does **not** mask app icons (unlike iOS): ship a 1024×1024 square with your
own rounded shape and margin. Transparency is fine.

## 5. Windows and Linux

Neither bundle is built. The Windows **transport** now exists
(`tltransport/windows.py`, WinSock `AF_BTH` / `BTHPROTO_RFCOMM` via ctypes), so a
Windows bundle is unblocked; Linux still needs a transport. The protocol and UI
are portable, so the packaging work is small once the transport is in place:

* **Windows** — PyInstaller produces a `.exe`/folder on Windows, and needs a
  Windows build host (PyInstaller does not cross-compile). The transport has
  been live-verified on hardware (`live-test.md`), so the Windows bundle is
  unblocked.
* **Linux** — PyInstaller produces an AppImage or a folder; the transport would
  use BlueZ over a plain `socket` (`BTPROTO_RFCOMM`). Needs a Linux build host.

Both need their own `Info.plist`-equivalent concerns: Windows wants an icon and
version resource, Linux a `.desktop` file and AppStream metadata. None of it
exists yet.
