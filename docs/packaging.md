# OpenRealLink — packaging and releases

Status: **macOS and Windows bundling implemented; Linux has no live transport
yet, so there is nothing to ship there.** This document covers what exists, how
to build it, and what a public release still needs.

---

## 1. The macOS bundle

A macOS **`OpenRealLink.app`**, frozen with [PyInstaller](https://pyinstaller.org)
from the repository checkout. It bundles the interpreter, PySide6, the pyobjc
`IOBluetooth` framework, `tlcore`, `tltransport`, `tlapp` and the replay
fixtures, so the app runs on a Mac with **no Python and no pip**. The Windows
build is described in §5.

The build is described by these files:

| File | Role |
| --- | --- |
| `release/macos/open-real-link.spec` | the PyInstaller recipe: entry point, hidden imports, bundled data, `Info.plist` |
| `release/macos/build.sh` | the wrapper: sets up the venv, runs PyInstaller, optionally makes a `.dmg` |
| `release/macos/entry.py` | the frozen entry point; calls `tlapp.app.main` |
| `release/macos/make-icon.sh` | turns `icon.svg` / `icon.png` into the `.icns` the bundle needs |

The bundle identifier is `io.github.anshwalia.openreallink` and the version is
read from `pyproject.toml`, so the bundle cannot drift from the package version.

## 2. Building (macOS)

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

## 3. Running the built app (macOS)

```console
$ open dist/macos/OpenRealLink.app
$ dist/macos/OpenRealLink.app/Contents/MacOS/OpenRealLink --replay spp-smoketest.jsonl
```

The command-line arguments are the same as `python -m tlapp` (`--address`,
`--connect`, `--replay`, …).

## 4. Signing and notarisation (macOS)

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

macOS app icons must be `.icns`; an SVG or PNG cannot be used directly. The
shared source is `release/icon.png`; drop an `icon.svg` next to the spec to use a
vector source instead, and let the helper build it:

```console
$ release/macos/make-icon.sh                  # icon.svg / release/icon.png -> icon.icns
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

## 5. The Windows bundle

A one-folder Windows build — `OpenRealLink\OpenRealLink.exe` plus its
dependencies — frozen with PyInstaller from the same checkout. It bundles the
interpreter, PySide6, `tlcore`, `tltransport`, `tlapp` and the replay fixtures,
so it runs on Windows with **no Python and no pip**.

| File | Role |
| --- | --- |
| `release/windows/open-real-link.spec` | the PyInstaller recipe: entry point, hidden imports, bundled data, icon, version resource |
| `release/windows/build.ps1` | the wrapper: sets up the venv, runs PyInstaller, optionally zips the result |
| `release/windows/build.cmd` | a thin `cmd.exe` wrapper around `build.ps1` |
| `release/windows/entry.py` | the frozen entry point; calls `tlapp.app.main` |
| `release/windows/make_icon.py` | packs `release/icon.png` into the `.ico` the build embeds |

### Building

From the repository root (PowerShell):

```console
PS> release\windows\build.ps1          # writes dist\windows\OpenRealLink\
PS> release\windows\build.ps1 -Zip     # ...and a versioned .zip beside it
```

`release\windows\build.cmd -Zip` does the same from `cmd.exe`. The script uses
the repo virtualenv (`.venv`) unless `%PYTHON%` is set, installs the
`[gui,release]` extras, and writes the result to `dist\windows\`.

| Path | What |
| --- | --- |
| `dist\windows\OpenRealLink\OpenRealLink.exe` | the app (no console window) |
| `dist\windows\OpenRealLink-<version>-win64.zip` | the distributable archive, with `-Zip` |

`dist\` and `build\` are ignored by git; nothing in them is committed.

### What is inside

* **The version resource** carries `ProductName`, `FileDescription`,
  `FileVersion` and friends, read from `pyproject.toml`, so the build cannot
  drift from the package version.
* **The icon** is built by `make_icon.py` from `release/icon.png` — the shared
  source, also used for the macOS `.icns`. It packs the PNG into a single-entry
  `.ico` with no external tools; export a large square source for the crispest
  taskbar and Explorer rendering.
* **Replay fixtures** are bundled next to the `tltransport` package inside
  `_internal`, so `--replay <fixture>` keeps working from the build, with no
  device.

The build is **one folder, not one file**: a one-file `.exe` unpacks to a temp
directory on every launch, which is slower to start and more likely to trip
antivirus heuristics. Zip the folder to distribute it.

### Signing

An unsigned `.exe` shows a SmartScreen warning on other machines ("Windows
protected your PC"). A public download should be signed with an Authenticode
certificate, after the build and **before** zipping:

```console
PS> signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
      /f cert.pfx /p <password> dist\windows\OpenRealLink\OpenRealLink.exe
```

An EV certificate earns SmartScreen reputation faster; a standard OV
certificate works but warns until it builds reputation.

### Releases

`.github/workflows/release.yml` builds this bundle on `windows-latest` and
attaches the `.zip` to a GitHub Release. PyInstaller does not cross-compile, so
the build must run on a Windows host. Push a `v*` tag to cut a release, or run
the workflow by hand to produce the artifact without releasing.

## 6. Linux

No bundle. Linux still needs a live transport (`tltransport/linux.py`, BlueZ
over a plain `socket` / `BTPROTO_RFCOMM`), and PyInstaller would need a Linux
build host. A `.desktop` file and AppStream metadata would come with it.
