# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OpenRealLink macOS app bundle.

Build from the repository root:

    pyinstaller --noconfirm --clean release/macos/open-real-link.spec

or use ``release/macos/build.sh``, which does the same inside the repo venv and
can also wrap the result in a ``.dmg``.

The bundle is named ``OpenRealLink.app`` and is signed ad-hoc by default, which
is enough to run it on the machine that built it. Distributing it to other
machines needs a Developer ID signature and notarisation -- see
``docs/packaging.md``.
"""
import pathlib
import re

# SPECPATH is injected by PyInstaller: the directory holding this spec file.
_HERE = pathlib.Path(SPECPATH).resolve()
ROOT = _HERE.parents[1]
FIXTURES = ROOT / "fixtures"
ICON = _HERE / "icon.icns"


def _project_version() -> str:
    """Read ``version`` from pyproject.toml so the bundle cannot drift from it."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


VERSION = _project_version()
APP_NAME = "OpenRealLink"
BUNDLE_ID = "io.github.anshwalia.openreallink"

# The replay transport finds fixtures next to the ``tltransport`` package, which
# in a frozen app is inside ``sys._MEIPASS``; dropping the directory in as a data
# file puts it in exactly that place, so ``--replay`` keeps working.
datas = [(str(FIXTURES), "fixtures")]

# pyobjc frameworks are imported dynamically, so PyInstaller cannot always see
# them from the import statements alone.
hiddenimports = [
    "objc",
    "Foundation",
    "AppKit",
    "IOBluetooth",
    "tlcore",
    "tltransport",
    "tltransport.macos",
    "tltransport.replay",
    "tlapp",
]

# Keep the bundle to the widgets stack; none of these are imported by the app.
excludes = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
]

a = Analysis(
    [str(_HERE / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

info_plist = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleExecutable": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "LSMinimumSystemVersion": "11.0",
    "LSApplicationCategoryType": "public.app-category.utilities",
    "NSHighResolutionCapable": True,
    # macOS 11+ terminates an app that touches Bluetooth without a usage
    # description; both keys are set so older systems are covered too.
    "NSBluetoothAlwaysUsageDescription": (
        "OpenRealLink uses Bluetooth to connect to and control your "
        "realme / OPPO earbuds."
    ),
    "NSBluetoothPeripheralUsageDescription": (
        "OpenRealLink uses Bluetooth to connect to and control your "
        "realme / OPPO earbuds."
    ),
}

bundle_kwargs = {
    "name": f"{APP_NAME}.app",
    "bundle_identifier": BUNDLE_ID,
    "info_plist": info_plist,
}
if ICON.exists():
    bundle_kwargs["icon"] = str(ICON)

app = BUNDLE(coll, **bundle_kwargs)
