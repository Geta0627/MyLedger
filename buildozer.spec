[app]

title = 轻记账
package.name = myledger
package.domain = com.example

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,ttf,ttc,csv
source.include_patterns = app/*, app/core/*, app/ocr/*, app/ui/*
source.exclude_dirs = .git, .github, bin, build, dist, data, tests, __pycache__, .venv, venv
source.exclude_patterns = test_*.py, *_test.py, *.md, .gitignore

version = 1.0.0

requirements = python3,kivy==2.3.0,requests,pillow,plyer,android

p4a.branch = v2023.09.16

android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a

android.permissions = INTERNET, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE, READ_MEDIA_IMAGES

orientation = portrait
android.manifest.orientation = portrait

android.allow_backup = True

android.logcat_filters = *:S python:D

android.no_compile_pyo = False

android.enable_androidx = True

android.accept_sdk_license = True

presplash.filename = %(source.dir)s/assets/presplash.png
icon.filename = %(source.dir)s/assets/icon.png

[buildozer]
log_level = 2
warn_on_root = 0
