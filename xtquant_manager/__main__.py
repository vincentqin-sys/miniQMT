# xtquant_manager/__main__.py
"""
python -m xtquant_manager 入口

用法:
    python -m xtquant_manager
    python -m xtquant_manager --config /path/to/config.json
    python -m xtquant_manager --host 0.0.0.0 --port 8888
    python -m xtquant_manager --help
"""
from .standalone import main

main()
