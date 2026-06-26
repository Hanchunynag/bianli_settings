#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
兼容入口。

旧命令：
  python .\settings_ui_manual_recorder.py

等价于：
  python .\settings_hdc_capture_runner.py

核心逻辑已拆分为：
- settings_json_tree_analyzer.py：脚本1，离线 JSON 分析与控件树创建；
- settings_hdc_capture_runner.py：脚本2，PC 端采集 JSON 后调用脚本1。
"""

from settings_hdc_capture_runner import main

if __name__ == "__main__":
    main()
