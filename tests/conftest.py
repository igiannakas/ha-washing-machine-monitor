"""Import this repo's custom_components package first, so Home Assistant's test harness loads
washing_machine_monitor from here rather than from its own testing_config folder."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import custom_components  # noqa: E402,F401
