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
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='待办桌宠', icon='assets/app.ico', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
