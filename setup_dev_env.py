#!/usr/bin/env python3
"""Set up a development environment for annealr.

Cross-platform (Linux, macOS, Windows). Creates a virtual environment
and installs test dependencies.

Usage:
    python setup_dev_env.py              # create dev-env and install deps
    python setup_dev_env.py --run-tests  # also run the test suite
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).parent.resolve()
VENV_DIR = REPO_ROOT / "dev-env"
REQUIREMENTS = ["pytest>=7.0", "pytest-cov>=4.0"]


def main():
    run_tests = "--run-tests" in sys.argv

    print("annealr development environment setup")
    print("=" * 50)
    print(f"Repository root: {REPO_ROOT}")
    print(f"Python version:  {sys.version.split()[0]}")
    print(f"Platform:        {sys.platform}")
    print(f"Venv location:   {VENV_DIR}")
    print()

    # Check Python version
    if sys.version_info < (3, 9):
        print("ERROR: Python 3.9+ is required. Current: %d.%d"
              % sys.version_info[:2])
        sys.exit(1)

    # Create venv
    if VENV_DIR.exists():
        print("Virtual environment already exists.")
        answer = input("Recreate it? [y/N] ").strip().lower()
        if answer == "y":
            import shutil
            shutil.rmtree(VENV_DIR)
        else:
            print("Keeping existing venv.")
    
    if not VENV_DIR.exists():
        print("Creating virtual environment...")
        venv.create(str(VENV_DIR), with_pip=True)
        print("  Created.")

    # Determine pip and python paths inside the venv
    if sys.platform == "win32":
        pip_bin = VENV_DIR / "Scripts" / "pip"
        python_bin = VENV_DIR / "Scripts" / "python"
    else:
        pip_bin = VENV_DIR / "bin" / "pip"
        python_bin = VENV_DIR / "bin" / "python"

    # Install dependencies
    print("Installing dependencies: %s" % ", ".join(REQUIREMENTS))
    subprocess.check_call(
        [str(pip_bin), "install", "--quiet"] + REQUIREMENTS)
    print("  Installed.")

    # Verify import works
    print("Verifying annealr package import...")
    result = subprocess.run(
        [str(python_bin), "-c",
         "import sys; sys.path.insert(0, 'src'); "
         "from annealr.profile import Segment; "
         "s = Segment('ramp', 200, ramp_rate=6); "
         "print('  OK: %s' % repr(s))"],
        cwd=str(REPO_ROOT),
        capture_output=True, text=True)
    if result.returncode != 0:
        print("  FAILED: %s" % result.stderr.strip())
        sys.exit(1)
    print(result.stdout.strip())

    # Run tests if requested
    if run_tests:
        print()
        print("Running tests...")
        print("-" * 50)
        result = subprocess.run(
            [str(python_bin), "-m", "pytest", "tests/unit/", "-v"],
            cwd=str(REPO_ROOT))
        sys.exit(result.returncode)

    # Print instructions
    print()
    print("=" * 50)
    print("Setup complete! To run tests:")
    print()
    if sys.platform == "win32":
        print("  dev-env\\Scripts\\activate")
        print("  pytest tests\\unit\\ -v")
        print()
        print("Or without activating:")
        print("  dev-env\\Scripts\\python -m pytest tests\\unit\\ -v")
    else:
        print("  source dev-env/bin/activate")
        print("  pytest tests/unit/ -v")
        print()
        print("Or without activating:")
        print("  dev-env/bin/python -m pytest tests/unit/ -v")
    print()


if __name__ == "__main__":
    main()
