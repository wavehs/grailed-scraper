# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

root_dir = Path(SPECPATH).resolve().parents[1]
backend_dir = root_dir / "backend"
frontend_out = root_dir / "frontend" / "out"
config_dir = root_dir / "config"

datas = [
    (str(frontend_out), "static"),
    (str(config_dir), "config"),
    (str(backend_dir / "alembic"), "alembic"),
    (str(backend_dir / "alembic.ini"), "."),
]

# Collect essential scraper and fingerprint data assets
datas += collect_data_files("browserforge")
datas += collect_data_files("apify_fingerprint_datapoints")
datas += collect_data_files("scrapling")
datas += collect_data_files("patchright")
datas += collect_data_files("curl_cffi")
datas += collect_data_files("certifi")

hiddenimports = [
    "uvicorn",
    "uvicorn.config",
    "uvicorn.main",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespans",
    "uvicorn.lifespans.on",
    "aiosqlite",
    "sqlalchemy.ext.asyncio",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "alembic",
    "alembic.config",
    "alembic.script",
    "alembic.runtime.environment",
    "scrapling",
    "scrapling.fetchers",
    "browserforge",
    "browserforge.headers",
    "browserforge.bayesian_network",
    "apify_fingerprint_datapoints",
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "pythonnet",
    "structlog",
    "structlog.stdlib",
    "pydantic",
    "pydantic_settings",
]
hiddenimports += collect_submodules("browserforge")
hiddenimports += collect_submodules("apify_fingerprint_datapoints")

a = Analysis(
    [str(backend_dir / "app" / "desktop.py")],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GrailedAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Windowed GUI application: NO black console window!
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GrailedAnalyzer",
)
