from ok import BaseTask


class eeee(BaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动按E"
        self.description = "每隔0.5秒按一次E"
        self.instructions = (
            '<a href="https://github.com/ok-oldking/ok-py">ok-py</a>'
        )

    def run(self):
        while True:
            self.sleep(0.8)
            self.send_key("e")