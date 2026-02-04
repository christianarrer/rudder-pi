#!/usr/bin/env python3
"""
Project bootstrap script.

- Creates a Python venv in ./.venv if it does not exist
- Upgrades pip inside the venv
- Installs required Python packages

Usage:
    python3 init.py
"""

import os
import sys
import subprocess
from pathlib import Path

VENV_DIR = Path(".venv")
REQUIREMENTS = [
    "gradio",
    "requests",
]

def run(cmd: list[str]) -> None:
    print(">>", " ".join(cmd))
    subprocess.check_call(cmd)

def main() -> None:
    if sys.prefix != sys.base_prefix:
        print("❌ Do not run init.py from inside a virtualenv.")
        sys.exit(1)

    python_exe = sys.executable

    # 1) Create venv if needed
    if not VENV_DIR.exists():
        print("📦 Creating virtual environment (.venv)")
        run([python_exe, "-m", "venv", str(VENV_DIR)])
    else:
        print("✅ Virtual environment already exists")

    # Resolve venv python & pip paths
    if os.name == "nt":
        venv_python = VENV_DIR / "Scripts" / "python"
        venv_pip = VENV_DIR / "Scripts" / "pip"
    else:
        venv_python = VENV_DIR / "bin" / "python"
        venv_pip = VENV_DIR / "bin" / "pip"

    # 2) Upgrade pip
    print("⬆️  Upgrading pip")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])

    # 3) Install requirements
    print("📥 Installing dependencies")
    run([str(venv_pip), "install", *REQUIREMENTS])

    print("\n🎉 Setup complete!")
    print("Activate with:")
    if os.name == "nt":
        print("  .venv\\Scripts\\activate")
    else:
        print("  source .venv/bin/activate")

if __name__ == "__main__":
    main()
