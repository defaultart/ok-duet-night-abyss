from ok import TriggerTask

from src.scene.DNAScene import DNAScene
from src.tasks.BaseDNATask import BaseDNATask
from src.tasks.trigger.AutoLiseUltimateTask import LiseUltimateMixin


class AutoYvonneUltimateTask(LiseUltimateMixin, BaseDNATask, TriggerTask):
    """伊薇 Q 消耗为 0 时已开启；正数消耗且能量大于 150 时自动开启。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "伊薇 Q 自动开启"
        self.description = "Q 关闭且黄色能量大于 150 时自动按 Q"
        self.scene: DNAScene | None = None
        self.reset_detection()

    def enable(self):
        self.reset_detection()
        return super().enable()

    def run(self):
        return self.check_toggle_ultimate(
            lambda: self.scene.in_team(self.in_team_and_world),
            character_name="伊薇", energy_threshold=150, max_cost=None,
        )
