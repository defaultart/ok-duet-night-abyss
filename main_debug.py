import ok
from src.config import config
from src.compat.gui_navigation import install_gui_navigation_fix
from src.compat.source_mode import disable_source_update_check
from src.compat.window_client_area import install_client_area_fix
from src.compat.window_capture_position import install_window_capture_position_fix

if __name__ == '__main__':
    config = config
    config['debug'] = True
    disable_source_update_check()
    install_gui_navigation_fix()
    install_client_area_fix(config)
    install_window_capture_position_fix()
    ok = ok.OK(config)
    ok.start()
