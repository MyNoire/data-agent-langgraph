"""数据工具的受控错误类别，不携带原始题目、路径或连接文本。"""

class DataPreparationError(ValueError):
    """数据输入或保护检查失败；错误类别供CLI记录，底层cause可保留。"""

    def __init__(self, error_type: str, message: str):
        """参数为受控类别与固定说明，返回None，无副作用。"""
        self.error_type = error_type
        super().__init__(message)
