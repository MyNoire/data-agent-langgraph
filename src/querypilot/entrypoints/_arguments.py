"""入口共用的安全参数解析；语法失败不回显用户原值。"""

import argparse
import sys


class SafeArgumentParser(argparse.ArgumentParser):
    """通过静态提示保留各入口的语法退出与用法输出。"""

    def __init__(self, *args, error_message: str = "querypilot: 参数不合法，请查看对应命令 --help\n",
                 show_usage_on_error: bool = False, **kwargs) -> None:
        """构造安全解析器。

        参数：args/kwargs 为 argparse 原有构造参数；error_message 为入口固定提示，
        show_usage_on_error 指定语法失败是否先打印静态用法。
        返回：无；副作用：仅初始化解析器，不读取参数或执行业务。
        """
        super().__init__(*args, **kwargs)
        self._error_message = error_message
        self._show_usage_on_error = show_usage_on_error

    def error(self, message: str) -> None:
        """参数：message 为含用户值的原始错误；返回：无；抛出 SystemExit(2)，仅输出静态提示。"""
        if self._show_usage_on_error:
            self.print_usage(sys.stderr)
        self.exit(2, self._error_message)
