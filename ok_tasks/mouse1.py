from ok import BaseTask

class mouse1(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "左键"
        self.description = "每隔1秒点击一次左键，持续运行直到手动停止"
        self.instructions = """<a href="https://github.com/ok-oldking/ok-py">ok-py</a>"""

    def run(self):
        # 仅在启动时记录一次，方便确认运行的是循环版本，避免每秒刷日志。
        self.logger.info("左键循环任务已启动，每隔1秒点击一次，直到手动停止")
        while True:
            # 使用任务自带的等待，让框架可以响应暂停和停止；每次点击前等待 1 秒。
            self.sleep(1.3)
            # 默认点击左键，不指定坐标以沿用框架当前的点击位置。
            self.click(key="left")
