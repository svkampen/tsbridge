def load_cfg_value(val: str) -> str:
    if val.startswith('file:'):
        path = val.split(':', 1)[1]
        with open(path, 'rb') as f:
            return f.read().decode('utf-8').strip()
    elif val.startswith('const:'):
        return val.split(':', 1)[1]
    raise ValueError("invalid config value")
