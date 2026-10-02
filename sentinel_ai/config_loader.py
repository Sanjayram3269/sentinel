import yaml
import os
from typing import Dict, Any

def load_config() -> Dict[str, Any]:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "default.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

config = load_config()
