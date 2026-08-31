# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")
pywinauto_datas, pywinauto_binaries, pywinauto_hiddenimports = collect_all("pywinauto")

a = Analysis(
    ["../src/jarvis/desktop.py"],
    pathex=["../src"],
    binaries=webview_binaries + pywinauto_binaries,
    datas=webview_datas + pywinauto_datas
    + [
        ("../web", "web"),
        ("../config/default.toml", "config"),
        ("../NOTICE.md", "."),
        ("../third_party_licenses", "third_party_licenses"),
        ("uia_click.ps1", "desktop"),
    ],
    hiddenimports=webview_hiddenimports + pywinauto_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["cefpython3", "PyQt5", "PyQt6", "PySide2", "PySide6", "kivy", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/jarvis.ico",
    version="version_info.txt",
    manifest="app.manifest",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
