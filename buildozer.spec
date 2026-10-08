[app]

# ---------------------------------------------------------------- 基本信息
title = 轻记账
package.name = myledger
package.domain = com.example

# 入口脚本
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,ttf,ttc,csv
source.include_patterns = app/*, app/core/*, app/ocr/*, app/ui/*
source.exclude_dirs = .git, .github, bin, build, dist, data, tests, __pycache__, .venv, venv
source.exclude_patterns = test_*.py, *_test.py, *.md, .gitignore

version = 1.0.0

# 依赖：kivy 是 GUI，requests 走 OCR 网络请求，pillow 做图片压缩
requirements = python3,kivy==2.3.0,requests,pillow,plyer,android

# ---------------------------------------------------------------- 安卓配置
# 28 = Android 9，覆盖面广且权限模型简单；想上架新版应用商店可提到 33+
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a

# 权限：网络（OCR必须）、读相册、写存储（导出CSV/存图片）
android.permissions = INTERNET, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE, READ_MEDIA_IMAGES

# 竖屏锁定——记账 APP 竖屏体验最佳
android.orientation = portrait

# 允许 manifest 加自定义配置（后面允许 http 明文可选）
android.allow_backup = True

# 日志级别：2=只输出错误，减少手机上的日志刷屏
android.logcat_filters = *:S python:D

# ---------------------------------------------------------------- 打包选项
# 每个架构独立 APK，体积更小；想要通用包改为 0
android.no_compile_pyo = False

# 压缩后 APK 体积约 25~35MB
android.enable_androidx = True

# 发布模式：debug 可直接安装；改 release 需要签名
# 命令行覆盖：buildozer android release
android.accept_sdk_license = True

# ---------------------------------------------------------------- 启动图
presplash.filename = %(source.dir)s/assets/presplash.png
icon.filename = %(source.dir)s/assets/icon.png

[buildozer]
log_level = 2
warn_on_root = 1
