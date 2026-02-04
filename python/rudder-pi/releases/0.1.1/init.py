#!/usr/bin/env python3
"""
Project bootstrap script.

Features:
- Creates a Python venv in ./.venv if it does not exist
- Upgrades pip inside the venv
- Installs required Python packages

Optional system provisioning (Raspberry Pi):
- Ensures PWM overlay line exists in config.txt (idempotent)
- Comments out dtparam=audio=on (optional, idempotent)
- Exports PWM channels 0 and 1 via sysfs (idempotent)
- Optionally installs a systemd oneshot service to export PWM channels at boot (idempotent)

Usage:
    python3 init.py
    python3 init.py --system
    python3 init.py --system --install-pwm-export-service
    python3 init.py --system --reboot
"""

from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional, Tuple


VENV_DIR = Path(".venv")

REQUIREMENTS_FILE = Path("requirements.txt")

# Keep these unpinned by default; pin later if you want reproducibility.
REQUIREMENTS = [
    # Web/API
    "fastapi",
    "uvicorn[standard]",
    "requests",
    "aiofiles",
    "python-multipart",
    "orjson",
    "PyYAML",
    # UI / tooling
    "gradio",
    "gradio_client",
    "rich",
    "typer",
    # Media
    "pillow",
    "pydub",
    "ffmpy",
    # Numeric/data
    "numpy",
    "pandas",
    # GPIO
    "pigpio",
]

PWM_OVERLAY_LINE = "dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4"
AUDIO_PARAM_LINE = "dtparam=audio=on"

SYSTEMD_SERVICE_NAME = "rudderpi-pwm-export.service"
SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system") / SYSTEMD_SERVICE_NAME


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print(">>", " ".join(cmd))
    return subprocess.run(cmd, check=check)


def sudo_run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    # Use sudo -n? No, because interactive password may be needed.
    return run(["sudo", *cmd], check=check)


def is_root() -> bool:
    return os.geteuid() == 0


def detect_config_path() -> Path:
    # Bookworm typically uses /boot/firmware/config.txt
    candidates = [
        Path("/boot/firmware/config.txt"),
        Path("/boot/config.txt"),
    ]
    for p in candidates:
        if p.exists():
            return p
    # If neither exists, return first candidate for error message context.
    return candidates[0]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text_atomic_as_root(path: Path, content: str) -> None:
    # Write via sudo tee to avoid permission issues and keep it simple.
    # Also create a backup if it doesn't exist yet.
    backup = Path(str(path) + ".bak")
    if path.exists() and not backup.exists():
        sudo_run(["cp", "-a", str(path), str(backup)])

    # Use tee to write full content
    p = subprocess.Popen(["sudo", "tee", str(path)], stdin=subprocess.PIPE, text=True)
    assert p.stdin is not None
    p.stdin.write(content)
    p.stdin.close()
    rc = p.wait()
    if rc != 0:
        raise subprocess.CalledProcessError(rc, ["sudo", "tee", str(path)])


def ensure_pwm_overlay_in_config(config_path: Path) -> Tuple[bool, bool]:
    """
    Returns (changed, reboot_recommended).
    """
    original = read_text(config_path) if config_path.exists() else ""
    lines = original.splitlines()

    changed = False
    reboot_recommended = False

    # 1) Ensure PWM overlay line exists (exact match). If missing, append.
    if PWM_OVERLAY_LINE not in lines:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("# Added by rudder-pi init.py (idempotent)")
        lines.append(PWM_OVERLAY_LINE)
        changed = True
        reboot_recommended = True

    # 2) Comment out dtparam=audio=on if present and not already commented
    new_lines = []
    for line in lines:
        if line.strip() == AUDIO_PARAM_LINE:
            new_lines.append("# " + line)
            changed = True
            reboot_recommended = True
        else:
            new_lines.append(line)

    new_content = "\n".join(new_lines) + "\n"
    if changed:
        write_text_atomic_as_root(config_path, new_content)

    return changed, reboot_recommended


def find_pwmchip_with_two_channels() -> Optional[Path]:
    base = Path("/sys/class/pwm")
    if not base.exists():
        return None

    chips = sorted(base.glob("pwmchip*"))
    for chip in chips:
        npwm_path = chip / "npwm"
        try:
            npwm = int(npwm_path.read_text().strip())
        except Exception:
            continue
        if npwm >= 2:
            return chip
    return None


def export_pwm_channels(pwmchip: Path, channels: list[int]) -> bool:
    """
    Export channels idempotently.
    Returns True if any change was made.
    """
    changed = False
    export_path = pwmchip / "export"
    for ch in channels:
        pwm_dir = pwmchip / f"pwm{ch}"
        if pwm_dir.exists():
            print(f"✅ {pwm_dir} already exists (channel {ch} already exported)")
            continue
        if not export_path.exists():
            raise RuntimeError(f"Export path does not exist: {export_path}")
        # Writing requires root.
        sudo_run(["bash", "-lc", f"echo {ch} > {export_path}"])
        print(f"✅ Exported channel {ch} at {pwmchip}")
        changed = True
    return changed


def install_systemd_pwm_export_service(pwmchip: Path) -> bool:
    """
    Installs a oneshot service that exports PWM channels 0 and 1 on boot.
    Idempotent: updates file only if content differs.
    Returns True if any change was made.
    """
    service_content = f"""[Unit]
Description=Export PWM channels for rudder-pi (sysfs)
DefaultDependencies=no
After=local-fs.target
Before=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -lc 'CHIP="{pwmchip}"; \
[ -d "$CHIP/pwm0" ] || echo 0 > "$CHIP/export"; \
[ -d "$CHIP/pwm1" ] || echo 1 > "$CHIP/export"'

[Install]
WantedBy=multi-user.target
"""
    changed = False

    existing = ""
    if SYSTEMD_SERVICE_PATH.exists():
        try:
            existing = read_text(SYSTEMD_SERVICE_PATH)
        except Exception:
            existing = ""

    if existing != service_content:
        # Write service file
        p = subprocess.Popen(["sudo", "tee", str(SYSTEMD_SERVICE_PATH)], stdin=subprocess.PIPE, text=True)
        assert p.stdin is not None
        p.stdin.write(service_content)
        p.stdin.close()
        rc = p.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, ["sudo", "tee", str(SYSTEMD_SERVICE_PATH)])
        changed = True
        print(f"✅ Wrote systemd service: {SYSTEMD_SERVICE_PATH}")

    # Enable service
    sudo_run(["systemctl", "daemon-reload"])
    sudo_run(["systemctl", "enable", SYSTEMD_SERVICE_NAME])
    print(f"✅ Enabled systemd service: {SYSTEMD_SERVICE_NAME}")

    return changed


def setup_venv_and_deps() -> None:
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
    if REQUIREMENTS_FILE.exists():
        print("📥 Installing dependencies from requirements.txt")
        run([str(venv_pip), "install", "-r", str(REQUIREMENTS_FILE)])
    else:
        print("📥 Installing dependencies from fallback list (requirements.txt missing)")
        run([str(venv_pip), "install", *FALLBACK_REQUIREMENTS])

    print("\n🎉 Python setup complete!")
    print("Activate with:")
    if os.name == "nt":
        print("  .venv\\Scripts\\activate")
    else:
        print("  source .venv/bin/activate")



def system_provision(install_service: bool, reboot: bool) -> None:
    config_path = detect_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Could not find config.txt at {config_path} (and no fallback exists)")

    print(f"🔧 Using config file: {config_path}")

    changed_cfg, reboot_needed = ensure_pwm_overlay_in_config(config_path)
    if changed_cfg:
        print("✅ Updated config.txt (PWM overlay / audio param)")
    else:
        print("✅ config.txt already configured (no changes needed)")

    # PWM sysfs export
    pwmchip = find_pwmchip_with_two_channels()
    if pwmchip is None:
        print("⚠️  No pwmchip with >=2 channels found under /sys/class/pwm.")
        print("    If you just changed config.txt, you likely need to reboot first.")
        if reboot_needed and reboot:
            sudo_run(["reboot"])
        return

    print(f"🔌 Using PWM chip: {pwmchip}")
    changed_export = export_pwm_channels(pwmchip, [0, 1])

    if install_service:
        changed_svc = install_systemd_pwm_export_service(pwmchip)
    else:
        changed_svc = False
        print("ℹ️  Skipping systemd service install (use --install-pwm-export-service to enable)")

    any_changes = changed_cfg or changed_export or changed_svc

    if reboot_needed:
        print("♻️  Reboot is recommended to ensure the overlay is applied.")
        if reboot and any_changes:
            print("🔁 Rebooting now...")
            sudo_run(["reboot"])
        else:
            print("    Run again after reboot; it is safe and idempotent.")
    else:
        print("✅ System provisioning complete (no reboot required).")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--system",
        action="store_true",
        help="Apply Raspberry Pi system changes (config.txt + PWM export). Requires sudo.",
    )
    p.add_argument(
        "--install-pwm-export-service",
        action="store_true",
        help="Install & enable a systemd oneshot service to export PWM channels at boot.",
    )
    p.add_argument(
        "--reboot",
        action="store_true",
        help="Reboot automatically if needed after changing config.txt.",
    )
    p.add_argument(
        "--dev",
        action="store_true",
        help="Install development dependencies (requirements-dev.txt)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Always do Python bootstrap
    setup_venv_and_deps()

    # Optional system provisioning
    if args.dev:
        install_requirements_file(venv_pip, Path("requirements-dev.txt"))
    else:
        print("ℹ️  Dev dependencies not requested (use --dev)")
    if args.system:
        # We don't force running as root, but we do require sudo for writes.
        print("\n🛠️  Applying system provisioning (sudo required)...")
        system_provision(
            install_service=args.install_pwm_export_service,
            reboot=args.reboot,
        )
    else:
        print("\nℹ️  System provisioning not run. If you want PWM overlay/export automation, run:")
        print("   python3 init.py --system --install-pwm-export-service")
        print("   (add --reboot if you want it to reboot automatically)")

    print("\n✅ Done.")


if __name__ == "__main__":
    main()

