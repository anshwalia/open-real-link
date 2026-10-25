# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OpenRealLink Windows build.

Build from the repository root:

    pyinstaller --noconfirm --clean release/windows/open-real-link.spec

or use ``release/windows/build.ps1``, which does the same inside the repo venv
and can also zip the result. The output is the one-folder build
``dist/windows/OpenRealLink/OpenRealLink.exe``, with no console window. An
unsigned build shows a SmartScreen warning on other machines; a public download
should be Authenticode-signed -- see ``docs/packaging.md``.
"""
import pathlib
import re

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

# SPECPATH is injected by PyInstaller: the directory holding this spec file.
_HERE = pathlib.Path(SPECPATH).resolve()
ROOT = _HERE.parents[1]
FIXTURES = ROOT / "fixtures"
ICON = _HERE / "icon.ico"


def _project_version() -> str:
    """Read ``version`` from pyproject.toml so the build cannot drift from it."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def _version_quad(version: str) -> tuple:
    """``0.0.1`` -> ``(0, 0, 1, 0)``; the resource wants exactly four numbers."""
    parts = [int(p) for p in re.findall(r"\d+", version)][:4]
    return tuple(parts + [0] * (4 - len(parts)))


VERSION = _project_version()
VERSION_QUAD = _version_quad(VERSION)
APP_NAME = "OpenRealLink"

# The replay transport finds fixtures next to the ``tltransport`` package, which
# in a frozen app is inside ``sys._MEIPASS``; dropping the directory in as a data
# file puts it in exactly that place, so ``--replay`` keeps working.
datas = [(str(FIXTURES), "fixtures")]

# The Windows transport is pure ctypes, so it has no dynamic imports to declare;
# the rest are listed for the same reason as in the macOS spec.
hiddenimports = [
    "tlcore",
    "tltransport",
    "tltransport.windows",
    "tltransport.replay",
    "tlapp",
]

# Keep the build to the widgets stack; none of these are imported by the app.
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

# The version resource shown in Explorer's Properties dialog. ProductName and
# FileVersion come from pyproject.toml so they cannot drift from the package.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=VERSION_QUAD,
        prodvers=VERSION_QUAD,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,    # VOS_NT_WINDOWS32
        fileType=0x1,  # VFT_APP
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [  # en-US, Unicode
                StringStruct("CompanyName", "open-real-link contributors"),
                StringStruct("FileDescription", APP_NAME),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", APP_NAME),
                StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", VERSION),
                StringStruct("LegalCopyright", "GPL-3.0-or-later"),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

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
    icon=str(ICON) if ICON.exists() else None,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
