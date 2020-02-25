import logging
import coloredlogs

FORMAT = "[%(asctime)s] [%(levelname)7s] %(name)7s: %(message)s"

def init(name: str = 'bridge', level: int = logging.INFO) -> None:
    fh = logging.FileHandler(f'{name}.log')
    fh.setLevel(level)

    sh = logging.StreamHandler()
    sh.setLevel(level)

    formatter = coloredlogs.ColoredFormatter(fmt=FORMAT, field_styles={
        "name": {"color": "red"},
        "levelname": {"color": "magenta"},
        "asctime": {"color": "cyan"}
    })

    sh.setFormatter(formatter)
    fh.setFormatter(formatter)

    logging.basicConfig(handlers=[sh, fh], level=level)
