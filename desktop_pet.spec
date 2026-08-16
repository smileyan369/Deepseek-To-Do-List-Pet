# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
datas = [('assets/character.json', 'assets'), ('assets/app-icon.png', 'assets'), ('assets/deepseek', 'assets/deepseek'), ('assets/minty', 'assets/minty')]
a = Analysis(['main.py'], pathex=[], binaries=[], datas=datas, hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='待办桌宠', icon='assets/app.ico', debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False)
