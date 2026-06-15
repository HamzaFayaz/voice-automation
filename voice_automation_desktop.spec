# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller build spec for the Voice Automation desktop tray app."""

from PyInstaller.utils.hooks import collect_all, collect_submodules


block_cipher = None


def collect_package(package_name):
    """Collect package data, binaries, and hidden imports when available."""
    try:
        return collect_all(package_name)
    except Exception:
        return [], [], []


datas = []
binaries = []
hiddenimports = [
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "sounddevice",
    "pynput",
    "websockets",
    "keyring",
    "moonshine",
    "moonshine_onnx",
    "moonshine_voice",
]

for package in (
    "PySide6",
    "sounddevice",
    "pynput",
    "websockets",
    "keyring",
    "moonshine",
    "moonshine_onnx",
    "moonshine_voice",
):
    package_datas, package_binaries, package_hiddenimports = collect_package(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for package in (
    "PySide6",
    "sounddevice",
    "pynput",
    "websockets",
    "keyring",
    "moonshine",
    "moonshine_onnx",
    "moonshine_voice",
):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

# Include assets directory (icon)
datas += [("voice_automation/assets", "voice_automation/assets")]

hiddenimports = sorted(set(hiddenimports))


a = Analysis(
    ["voice_automation\\desktop.py"],
    pathex=[],
    binaries=binaries,
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
    name="VoiceAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="voice_automation/assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VoiceAutomation",
)
