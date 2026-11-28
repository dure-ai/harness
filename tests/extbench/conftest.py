import sys
from pathlib import Path

# extbench is a tool run from its own directory (tools/extbench/run.sh), not an installed package.
sys.path.insert(0, str(Path(__file__).parents[2] / "tools" / "extbench"))
