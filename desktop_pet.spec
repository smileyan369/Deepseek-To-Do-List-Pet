# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
# Modules core/runtime_guard.py imports up front so a %TEMP% cleanup cannot break
# a running pet; they are imported by name, so list them for static analysis.
hiddenimports = ['ssl', '_ssl', 'socket', 'select', 'gzip', 'hmac', 'encodings.idna']
datas = [('assets/character.json', 'assets'), ('assets/app-icon.png', 'assets'), ('assets/deepseek', 'assets/deepseek'), ('assets/minty', 'assets/minty')]
a = Analysis(['main.py'], pathex=[], binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
# upx=False: UPX-packed DLLs make antivirus and temp cleaners more likely to touch
# the extraction directory this app depends on.
#
# runtime_tmpdir: the single-file bootloader unpacks its whole runtime before any
# Python code runs, and it defaults to %TEMP%\_MEIxxxxxx. On this machine %TEMP% is
# actively cleaned (4.5 GB observed, and _MEI directories were being removed while a
# launch was in flight), which surfaced as "Could not create temporary directory!"
# from the bootloader. Unpacking under the app's own LOCALAPPDATA tree keeps the
# extraction away from temp cleaners and out of the roaming profile; the bootloader
# expands the environment variable at run time. PyInstaller 6 passes this through
# to the bootloader as an OPTION entry (verified working in the built exe).
runtime_tmpdir = r'%LOCALAPPDATA%\深海待办桌宠\runtime'
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='待办桌宠', icon='assets/app.ico', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, runtime_tmpdir=runtime_tmpdir)
