"""轻记账 —— 应用入口。

打包 APK 时 buildozer 会以本文件为起点。
桌面端调试：python3 main.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ui.main import LedgerApp

if __name__ == "__main__":
    LedgerApp().run()
