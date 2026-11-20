"""把仓库内的 src/ 加入 sys.path，使测试可在不设置 PYTHONPATH 时运行。"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
