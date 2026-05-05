# Pytest configuration and shared fixtures for annealr unit tests
import sys
from pathlib import Path

# Add the plugin's src path so we can import the annealr package
PLUGIN_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "src"))
