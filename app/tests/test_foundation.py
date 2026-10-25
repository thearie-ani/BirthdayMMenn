"""Foundation and environment sanity tests."""
import importlib
import os


def test_required_packages_installed():
    """Verify that all core dependencies can be imported successfully."""
    core_packages = [
        "telegram",
        "sqlalchemy",
        "apscheduler",
        "dotenv",
        "pytest",
    ]
    for package in core_packages:
        module = importlib.import_module(package)
        assert module is not None


def test_env_example_exists():
    """Verify that the template environment file exists."""
    assert os.path.exists(".env.example")
