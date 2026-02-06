#!/usr/bin/env python3
"""
Project bootstrap + Raspberry Pi provisioning script.

Features:
- Creates a Python venv in ./.venv if it does not exist
- Upgrades pip inside the venv
- Inst:contentReference[oaicite:6]{index=6}packages

Optional system provisioning (Raspberry Pi):
- Ensures PWM overlay line exists in config.txt (idempotent)
- Comments out dtparam=audio=on (optional, idempotent)
- Exports PWM channels 0 and 1 via sysfs (idempotent)
- Optionally installs a systemd oneshot service to export PWM channels at boot (idempotent)

Additional provisioning (v0.2.0+):
- Set hostname to "rudder-pi" (idempotent)
- Install + enable + start avahi-daemon (mDNS: rudder-pi.local) (idempotent)
- Install MediaMTX (apt if available, otherwise GitHub release fallback) (idempotent)
- Ensure MediaMTX config contains:
    paths:
      rudderpi:
        source: publisher
  (idempotent, safe to run multiple times)
- Install + enable + start mediamtx systemd service (idempotent)

Usage:
    python3 setup.py
    python3 setup.py --system
    python3 setup.py --system --install-pwm-export-service
    python3 setup.py --system --install-pwm-export-service --reboot
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
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

SYSTEMD_PWM_SERVICE_NAME = "rudderpi-pwm-export.service"
SYSTEMD_PWM_SERVICE_PATH = Path("/etc/systemd/system") / SYSTEMD_PWM_SERVICE_NAME

# Hostname / Avahi
TARGET_HOSTNAME = "rudder-pi"

# MediaMTX
MEDIAMTX_BIN = Path("/usr/local/bin/mediamtx")
MEDIAMTX_ETC_DIR = Path("/etc/mediamtx")
MEDIAMTX_CONFIG = MEDIAMTX_ETC_DIR / "mediamtx.yml"
MEDIAMTX_SERVICE_NAME = "mediamtx.service"
MEDIAMTX_SERVICE_PATH = Path("/etc/systemd/system") / MEDIAMTX_SERVICE_NAME

# This is the YAML fragment we want to exist in the config file.
MEDIAMTX_PATHS_FRAGMENT = (
    "paths:\n"
    "  rudderpi:\n"
    "    source: publisher\n"
)
# MediaMTX hardcoded download (stable known-good)
MEDIAMTX_VERSION = "v1.16.0"
MEDIAMTX_ARCH = "linux_arm64"   # or linux_armv7, linux_amd64
MEDIAMTX_URL = (
    f"https://github.com/bluenviron/mediamtx/releases/download/"
    f"{MEDIAMTX_VERSION}/mediamtx_{MEDIAMTX_VERSION}_{MEDIAMTX_ARCH}.tar.gz"
)


def run(cmd: list[str], *, check: bool = True, capture: bool = False, text: bool = True) -> subprocess.CompletedProcess:
    print(">>", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=capture, text=text)


def sudo_run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return run(["sudo", *cmd], check=check, capture=capture)


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
    return candidates[0]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text_atomic_as_root(path: Path, content: str) -> None:
    backup = Path(str(path) + ".bak")
    if path.exists() and not backup.exists():
        sudo_run(["cp", "-a", str(path), str(backup)])

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

    if PWM_OVERLAY_LINE not in lines:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("# Added by rudder-pi setup.py (idempotent)")
        lines.append(PWM_OVERLAY_LINE)
        changed = True
        reboot_recommended = True

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
    changed = False
    export_path = pwmchip / "export"
    for ch in channels:
        pwm_dir = pwmchip / f"pwm{ch}"
        if pwm_dir.exists():
            print(f"✅ {pwm_dir} already exists (channel {ch} already exported)")
            continue
        if not export_path.exists():
            raise RuntimeError(f"Export path does not exist: {export_path}")
        sudo_run(["bash", "-lc", f"echo {ch} > {export_path}"])
        print(f"✅ Exported channel {ch} at {pwmchip}")
        changed = True
    return changed


def install_systemd_pwm_export_service(pwmchip: Path) -> bool:
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
    existing = read_text(SYSTEMD_PWM_SERVICE_PATH) if SYSTEMD_PWM_SERVICE_PATH.exists() else ""
    if existing != service_content:
        p = subprocess.Popen(["sudo", "tee", str(SYSTEMD_PWM_SERVICE_PATH)], stdin=subprocess.PIPE, text=True)
        assert p.stdin is not None
        p.stdin.write(service_content)
        p.stdin.close()
        rc = p.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, ["sudo", "tee", str(SYSTEMD_PWM_SERVICE_PATH)])
        changed = True
        print(f"✅ Wrote systemd service: {SYSTEMD_PWM_SERVICE_PATH}")

    sudo_run(["systemctl", "daemon-reload"])
    sudo_run(["systemctl", "enable", SYSTEMD_PWM_SERVICE_NAME])
    print(f"✅ Enabled systemd service: {SYSTEMD_PWM_SERVICE_NAME}")
    return changed


def setup_venv_and_deps() -> None:
    if sys.prefix != sys.base_prefix:
        print("❌ Do not run setup.py from inside a virtualenv.")
        sys.exit(1)

    python_exe = sys.executable

    if not VENV_DIR.exists():
        print("📦 Creating virtual environment (.venv)")
        run([python_exe, "-m", "venv", str(VENV_DIR)])
    else:
        print("✅ Virtual environment already exists")

    if os.name == "nt":
        venv_python = VENV_DIR / "Scripts" / "python"
        venv_pip = VENV_DIR / "Scripts" / "pip"
    else:
        venv_python = VENV_DIR / "bin" / "python"
        venv_pip = VENV_DIR / "bin" / "pip"

    print("⬆️  Upgrading pip")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])

    print("📥 Installing dependencies")
    if REQUIREMENTS_FILE.exists():
        print("📥 Installing dependencies from requirements.txt")
        run([str(venv_pip), "install", "-r", str(REQUIREMENTS_FILE)])
    else:
        print("📥 Installing dependencies from fallback list (requirements.txt missing)")
        run([str(venv_pip), "install", *REQUIREMENTS])

    print("\n🎉 Python setup complete!")
    print("Activate with:")
    if os.name == "nt":
        print("  .venv\\Scripts\\activate")
    else:
        print("  source .venv/bin/activate")


def apt_install(pkgs: list[str]) -> bool:
    """
    Install packages with apt-get idempotently.
    Returns True if anything was installed (best-effort).
    """
    sudo_run(["apt-get", "update"])
    # apt-get install is idempotent; we don't try to detect changes precisely.
    sudo_run(["apt-get", "install", "-y", *pkgs])
    return True


def ensure_hostname(target: str) -> bool:
    """
    Ensure the system hostname matches target and persists across reboots.

    Handles cloud-init setups (common on some images) by:
      - preserve_hostname: true
      - manage_etc_hosts: false

    Also updates:
      - /etc/hostname
      - /etc/hosts (127.0.1.1 entry)

    Returns True if any changes were made.
    """
    changed = False

    # 1) Ensure cloud-init won't revert hostname / hosts
    cloud_cfg_dir = Path("/etc/cloud/cloud.cfg.d")
    cloud_cfg_path = cloud_cfg_dir / "99-rudderpi-hostname.cfg"
    desired_cloud_cfg = "preserve_hostname: true\nmanage_etc_hosts: false\n"

    sudo_run(["mkdir", "-p", str(cloud_cfg_dir)])
    existing_cloud_cfg = read_text(cloud_cfg_path) if cloud_cfg_path.exists() else ""
    if existing_cloud_cfg != desired_cloud_cfg:
        write_text_atomic_as_root(cloud_cfg_path, desired_cloud_cfg)
        print(f"✅ Wrote cloud-init override: {cloud_cfg_path}")
        changed = True

    # 2) Set runtime/static hostname
    current = run(["hostnamectl", "--static"], check=True, capture=True).stdout.strip()
    if current != target:
        print(f"🔧 Changing hostname: '{current}' -> '{target}'")
        sudo_run(["hostnamectl", "set-hostname", target])
        changed = True
    else:
        print(f"✅ Hostname already '{target}'")

    # 3) Ensure /etc/hostname persists
    hostname_path = Path("/etc/hostname")
    existing_hostname = read_text(hostname_path).strip() if hostname_path.exists() else ""
    if existing_hostname != target:
        write_text_atomic_as_root(hostname_path, target.strip() + "\n")
        print("✅ Updated /etc/hostname")
        changed = True

    # 4) Fix /etc/hosts to avoid sudo 'unable to resolve host ...' warnings
    hosts_path = Path("/etc/hosts")
    hosts = read_text(hosts_path)
    lines = hosts.splitlines()

    new_lines: list[str] = []
    found_127_0_1_1 = False
    for line in lines:
        if line.strip().startswith("127.0.1.1"):
            found_127_0_1_1 = True
            # Replace the full line with a stable mapping.
            new_lines.append(f"127.0.1.1\t{target}")
        else:
            new_lines.append(line)

    if not found_127_0_1_1:
        # Add a Debian-style hostname mapping if missing
        new_lines.append(f"127.0.1.1\t{target}")

    new_hosts = "\n".join(new_lines).rstrip() + "\n"
    if new_hosts != hosts:
        write_text_atomic_as_root(hosts_path, new_hosts)
        print("✅ Updated /etc/hosts")
        changed = True

    if changed:
        print("♻️ Hostname/cloud-init changed. A reboot is recommended so everything picks it up.")

    return changed



def ensure_avahi() -> bool:
    """
    Ensure avahi-daemon is installed and running (mDNS: hostname.local).
    Returns True if we made changes (best-effort).
    """
    print("📦 Ensuring avahi-daemon is installed")
    apt_install(["avahi-daemon", "libnss-mdns"])

    print("✅ Enabling + starting avahi-daemon")
    sudo_run(["systemctl", "enable", "--now", "avahi-daemon"])

    # Ensure avahi daemon uses hostname (defaults usually OK; keep minimal edits)
    conf = Path("/etc/avahi/avahi-daemon.conf")
    if conf.exists():
        txt = read_text(conf)
        # Ensure 'use-ipv4=yes' and 'use-ipv6=yes' not touched; we only ensure publish settings.
        # Keep this minimal to avoid surprising config changes.
        return True
    return True


def _machine_arch_for_mediamtx() -> str:
    """
    Map machine arch to MediaMTX release asset suffix.
    """
    m = platform.machine().lower()
    if m in ("aarch64", "arm64"):
        return "linux_arm64"
    if m.startswith("armv7") or m == "arm":
        return "linux_armv7"
    if m in ("x86_64", "amd64"):
        return "linux_amd64"
    raise RuntimeError(f"Unsupported architecture for MediaMTX auto-install: {m}")


def _download_mediamtx_tarball(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "mediamtx.tar.gz"

    sudo_run([
        "bash", "-lc",
        f"curl -fL '{MEDIAMTX_URL}' -o '{out}'"
    ])

    return out


def ensure_mediamtx_installed() -> bool:
    """
    Ensure mediamtx binary exists.
    Prefer apt (if package exists), otherwise fallback to GitHub release download.
    Returns True if installed/changed.
    """
    if MEDIAMTX_BIN.exists():
        print(f"✅ MediaMTX already installed at {MEDIAMTX_BIN}")
        return False

    print("📦 Trying to install mediamtx via apt (if available)")
    # Best-effort: apt package may not exist on all RPi OS repos.
    apt_ok = True
    try:
        sudo_run(["apt-get", "update"])
        # --no-install-recommends keeps it lean
        sudo_run(["apt-get", "install", "-y", "--no-install-recommends", "mediamtx"])
    except subprocess.CalledProcessError:
        apt_ok = False

    # Some distros might install to /usr/bin/mediamtx
    if apt_ok:
        for candidate in (Path("/usr/bin/mediamtx"), Path("/usr/local/bin/mediamtx"), Path("/bin/mediamtx")):
            if candidate.exists():
                if candidate != MEDIAMTX_BIN:
                    sudo_run(["cp", "-a", str(candidate), str(MEDIAMTX_BIN)])
                print(f"✅ MediaMTX installed via apt at {candidate}")
                return True

    print("⬇️  Apt install not available; installing MediaMTX via GitHub release tarball")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        tgz = _download_mediamtx_tarball(td_path)
        extract_dir = td_path / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        # Extract tarball
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(path=extract_dir)

        extracted_bin = extract_dir / "mediamtx"
        extracted_cfg = extract_dir / "mediamtx.yml"

        if not extracted_bin.exists():
            raise RuntimeError("Downloaded tarball did not contain 'mediamtx' binary")

        # Install binary
        sudo_run(["install", "-m", "0755", str(extracted_bin), str(MEDIAMTX_BIN)])
        print(f"✅ Installed MediaMTX binary to {MEDIAMTX_BIN}")

        # Seed config dir if missing; keep original as reference
        sudo_run(["mkdir", "-p", str(MEDIAMTX_ETC_DIR)])
        if extracted_cfg.exists() and not MEDIAMTX_CONFIG.exists():
            sudo_run(["install", "-m", "0644", str(extracted_cfg), str(MEDIAMTX_CONFIG)])
            print(f"✅ Seeded MediaMTX config to {MEDIAMTX_CONFIG}")

    return True


def ensure_mediamtx_config_paths() -> bool:
    """
    Ensure mediamtx.yml contains:
      paths:
        rudderpi:
          source: publisher
    Idempotent text-based patch (no YAML dependency).
    Returns True if changed.
    """
    sudo_run(["mkdir", "-p", str(MEDIAMTX_ETC_DIR)])

    existing = ""
    if MEDIAMTX_CONFIG.exists():
        existing = read_text(MEDIAMTX_CONFIG)

    if "paths:" not in existing:
        # Append a minimal paths section
        new_content = (existing.rstrip() + "\n\n" + MEDIAMTX_PATHS_FRAGMENT)
        write_text_atomic_as_root(MEDIAMTX_CONFIG, new_content)
        print(f"✅ Added paths section to {MEDIAMTX_CONFIG}")
        return True

    # If paths exists, ensure rudderpi block exists under it.
    if re.search(r"(?m)^\s{2}rudderpi:\s*$", existing) and re.search(r"(?m)^\s{4}source:\s*publisher\s*$", existing):
        print("✅ MediaMTX paths.rudderpi.source already configured")
        return False

    # Insert rudderpi block right after the first 'paths:' line if not present
    lines = existing.splitlines()
    out_lines: list[str] = []
    inserted = False
    for i, line in enumerate(lines):
        out_lines.append(line)
        if not inserted and line.strip() == "paths:":
            # Insert our block only if rudderpi is not already present anywhere
            if not re.search(r"(?m)^\s{2}rudderpi:\s*$", existing):
                out_lines.append("  rudderpi:")
                out_lines.append("    source: publisher")
                inserted = True

    new_content = "\n".join(out_lines).rstrip() + "\n"
    if new_content != existing:
        write_text_atomic_as_root(MEDIAMTX_CONFIG, new_content)
        print(f"✅ Patched {MEDIAMTX_CONFIG} to include paths.rudderpi.source=publisher")
        return True

    print("✅ MediaMTX config already OK (no changes)")
    return False


def ensure_mediamtx_service() -> bool:
    """
    Ensure systemd service exists, enabled, and started.
    Runs mediamtx with explicit config path.
    Returns True if changed.
    """
    service_content = f"""[Unit]
Description=MediaMTX (rudder-pi)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={MEDIAMTX_BIN} {MEDIAMTX_CONFIG}
Restart=on-failure
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
"""
    changed = False
    existing = read_text(MEDIAMTX_SERVICE_PATH) if MEDIAMTX_SERVICE_PATH.exists() else ""
    if existing != service_content:
        p = subprocess.Popen(["sudo", "tee", str(MEDIAMTX_SERVICE_PATH)], stdin=subprocess.PIPE, text=True)
        assert p.stdin is not None
        p.stdin.write(service_content)
        p.stdin.close()
        rc = p.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, ["sudo", "tee", str(MEDIAMTX_SERVICE_PATH)])
        changed = True
        print(f"✅ Wrote systemd service: {MEDIAMTX_SERVICE_PATH}")

    sudo_run(["systemctl", "daemon-reload"])
    sudo_run(["systemctl", "enable", "--now", MEDIAMTX_SERVICE_NAME])
    print("✅ Enabled + started mediamtx")
    return changed


def system_provision(install_pwm_service: bool, reboot: bool) -> None:
    reboot_recommended = False

    # 1) Hostname + Avahi (mDNS)
    changed_host = ensure_hostname(TARGET_HOSTNAME)
    if changed_host:
        reboot_recommended = True

    ensure_avahi()

    # 2) PWM overlay + export
    config_path = detect_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Could not find config.txt at {config_path} (and no fallback exists)")

    print(f"🔧 Using config file: {config_path}")
    changed_cfg, reboot_needed = ensure_pwm_overlay_in_config(config_path)
    reboot_recommended = reboot_recommended or reboot_needed

    if changed_cfg:
        print("✅ Updated config.txt (PWM overlay / audio param)")
    else:
        print("✅ config.txt already configured (no changes needed)")

    pwmchip = find_pwmchip_with_two_channels()
    if pwmchip is None:
        print("⚠️  No pwmchip with >=2 channels found under /sys/class/pwm.")
        print("    If you just changed config.txt, you likely need to reboot first.")
        if reboot_recommended and reboot:
            sudo_run(["reboot"])
        return

    print(f"🔌 Using PWM chip: {pwmchip}")
    changed_export = export_pwm_channels(pwmchip, [0, 1])

    if install_pwm_service:
        changed_pwm_svc = install_systemd_pwm_export_service(pwmchip)
    else:
        changed_pwm_svc = False
        print("ℹ️  Skipping PWM export service install (use --install-pwm-export-service)")

    # 3) MediaMTX
    changed_mtx = ensure_mediamtx_installed()
    changed_mtx_cfg = ensure_mediamtx_config_paths()
    changed_mtx_svc = ensure_mediamtx_service()

    any_changes = changed_host or changed_cfg or changed_export or changed_pwm_svc or changed_mtx or changed_mtx_cfg or changed_mtx_svc

    if reboot_recommended:
        print("♻️  Reboot is recommended (hostname and/or overlay changes).")
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
        help="Apply Raspberry Pi system changes (hostname+avahi+mediamtx+config.txt + PWM export). Requires sudo.",
    )
    p.add_argument(
        "--install-pwm-export-service",
        action="store_true",
        help="Install & enable a systemd oneshot service to export PWM channels at boot.",
    )
    p.add_argument(
        "--reboot",
        action="store_true",
        help="Reboot automatically if recommended after changes.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    setup_venv_and_deps()

    if args.system:
        print("\n🛠️  Applying system provisioning (sudo required)...")
        system_provision(
            install_pwm_service=args.install_pwm_export_service,
            reboot=args.reboot,
        )
    else:
        print("\nℹ️  System provisioning not run. If you want the full Pi setup, run:")
        print("   python3 setup.py --system --install-pwm-export-service")
        print("   (add --reboot if you want it to reboot automatically)")

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
