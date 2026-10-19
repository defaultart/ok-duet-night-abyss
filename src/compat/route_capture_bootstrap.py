"""Let route sessions initialize before the executor's eager first capture.

Route sessions own bounded capture and keyboard-listener setup. The default
executor fetches a frame before run(), which can block both setup and Stop.
All other tasks keep their original capture behavior.
"""
from functools import wraps


def install_route_capture_bootstrap(executor_class):
    if getattr(executor_class, '_dna_route_capture_bootstrap', False):
        return
    original = executor_class.next_frame

    @wraps(original)
    def next_frame(self, *args, **kwargs):
        task = self.current_task
        if (getattr(task, '_route_owns_capture', False)
                and getattr(task, 'session', None) is not None
                and not getattr(task, '_route_in_run', False)):
            return None
        return original(self, *args, **kwargs)

    executor_class.next_frame = next_frame
    executor_class._dna_route_capture_bootstrap = True
