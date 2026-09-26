import ok
from src.config import config
from src.compat.source_mode import disable_source_update_check
from src.compat.window_client_area import install_client_area_fix

if __name__ == '__main__':
    config = config
    config['debug'] = True
    disable_source_update_check()
    install_client_area_fix(config)
    ok = ok.OK(config)
    ok.start()
