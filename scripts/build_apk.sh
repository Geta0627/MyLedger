#!/usr/bin/env bash
# 一键本地打包 APK（仅 Linux / WSL2 可用）
#
# 用法：bash scripts/build_apk.sh [debug|release]
# 产物：bin/*.apk
#
# 如果你没有 Linux 环境，请用 GitHub Actions 云端构建，见 README。

set -euo pipefail

MODE="${1:-debug}"
cd "$(dirname "$0")/.."

echo "=============================================="
echo " 轻记账 · 本地打包 ($MODE)"
echo "=============================================="

# ---------- 环境检查 ----------
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "✗ 错误：Buildozer 打包 APK 只支持 Linux 或 WSL2。"
    echo "  当前系统：$(uname -s)"
    echo ""
    echo "  推荐改用 GitHub Actions 云端构建，见 README.md「快速开始」章节。"
    exit 1
fi

# ---------- 系统依赖 ----------
MISSING=()
for cmd in git zip unzip java; do
    command -v "$cmd" >/dev/null 2>&1 || MISSING+=("$cmd")
done
if (( ${#MISSING[@]} > 0 )); then
    echo "→ 缺少依赖：${MISSING[*]}，正在安装…"
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        git zip unzip openjdk-17-jdk-headless autoconf libtool pkg-config \
        zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo6 \
        cmake libffi-dev libssl-dev automake
fi

# ---------- Python 依赖 ----------
if ! command -v buildozer >/dev/null 2>&1; then
    echo "→ 安装 buildozer…"
    pip install --user buildozer 'cython==0.29.36' virtualenv
    export PATH="$HOME/.local/bin:$PATH"
fi

export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"

# ---------- 先跑测试 ----------
echo ""
echo "→ 运行核心逻辑测试…"
python3 -m pip install -q requests pillow
python3 tests/run_tests.py || {
    echo "✗ 测试未通过，中止打包。"
    exit 1
}

# ---------- 构建 ----------
echo ""
echo "→ 开始构建（首次约 25-40 分钟，需下载 Android SDK/NDK）…"
buildozer -v "android" "$MODE"

echo ""
echo "=============================================="
echo " ✓ 构建完成，产物："
ls -lh bin/*.apk 2>/dev/null || echo "  （未找到 APK，请检查上方日志）"
echo "=============================================="
echo ""
echo "安装到手机："
echo "  1. 把 APK 传到手机"
echo "  2. 系统设置里允许「安装未知来源应用」"
echo "  3. 点击安装"
