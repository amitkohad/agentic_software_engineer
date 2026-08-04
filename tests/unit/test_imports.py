import importlib
import sys


def test_package_importable_from_repo_root() -> None:
    sys.modules.pop("agentic_software_engineer", None)
    module = importlib.import_module("agentic_software_engineer")
    assert module.__name__ == "agentic_software_engineer"
