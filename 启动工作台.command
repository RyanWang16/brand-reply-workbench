#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3。请先安装 Python 3.9 或更新版本。"
  read -r -p "按回车键关闭窗口。"
  exit 1
fi
python3 app.py --open
