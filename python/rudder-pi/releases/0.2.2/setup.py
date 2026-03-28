#!/usr/bin/env python3
"""
Project bootstrap + Raspberry Pi provisioning script (rudder-pi).

Features:
- Creates a Python venv in ./.venv if it does not exist
- Upgrades pip inside the venv
- Installs Python requirements

Optional system provisioning (Raspberry Pi):
- Ensures PWM overlay line exists in config.txt (idempotent)
- Comments out dtparam=audio=on (idempotent)
- Exports PWM channels 0 and 1 via sysfs (idempotent)
- Optionally installs a systemd oneshot service to export PWM channels at boot (idempotent)

Additional provisioning (v0.2.2):
- Set hostname to "rudder-pi" (idempotent)
- Install + enable + start avahi-daemon (mDNS: rudder-pi.local) (idempotent)
- Install MediaMTX (apt if available, otherwise GitHub release fallback) (idempotent)
- Ensure MediaMTX config contains:
    - webrtcICEServers2 TURN entry
    - paths: rudderpiraw and rudderpi (with optional ffmpeg runOnDemand)
- Install + enable + start mediamtx systemd service (idempotent)
- Install + enable + start systemd units:
    - rudder-pi.service (app.py)
    - rudderpi-ping-lan.timer (LAN keepalive ping)
    - rudderpi-provision-ip.timer (retry phone IP provisioning)
- Ensure default systemd target is multi-user.target (no GUI by default)

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
import shlex
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

# MediaMTX hardcoded version (stable known-good)
MEDIAMTX_VERSION = "v1.16.0"

# This is the YAML fragment we want to exist in the config file.
MEDIAMTX_WEBRTC_ICE_FRAGMENT = (
    "webrtcICEServers2:\n"
    "  - url: turn:rudder-pi-webrtc.schrottplatz.internal:3478?transport=udp\n"
    "    username: rudderpi\n"
    "    password: rudderpi42\n"
)

MEDIAMTX_PATHS_FRAGMENT = (
    "paths:\n"
    "  rudderpiraw:\n"
    "    source: publisher\n"
    "\n"
    "  rudderpi:\n"
    "    source: publisher\n"
    "    runOnDemand: >\n"
    "      ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp "
    "-i rtsp://127.0.0.1:8554/rudderpiraw -an -vf \"transpose=1\" "
    "-c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p "
    "-g 30 -keyint_min 30 -b:v 1000k -maxrate 1800k -bufsize 6000k "
    "-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/rudderpi\n"
    "    runOnDemandRestart: yes\n"
)

SYSTEMD_DIR = Path("/etc/systemd/system")

RUDDER_PI_SERVICE = """\
[Unit]
Description=Rudder Pi Service
Wants=network-online.target
After=network-online.target
After=wg-quick@wg0.service

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/opt/rudder-pi

# Optional: don't fail if env file is missing (leading "-")
EnvironmentFile=-/opt/rudder-pi/env

# Ensure immediate logs in journalctl and predictable behavior
Environment=PYTHONUNBUFFERED=1

ExecStart=/opt/rudder-pi/.venv/bin/python -u /opt/rudder-pi/app.py

Restart=on-failure
RestartSec=2

# Optional hardening (adjust if needed)
NoNewPrivileges=false
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
"""

RUDDERPI_PING_LAN_SERVICE = """\
[Unit]
Description=Keep LAN connection alive by pinging 192.168.21.80

[Service]
Type=oneshot
ExecStart=/bin/ping -c 1 -W 1 192.168.21.80
"""

RUDDERPI_PING_LAN_TIMER = """\
[Unit]
Description=Run rudderpi-ping-lan.service every 20 seconds

[Timer]
OnBootSec=30
OnUnitActiveSec=20
AccuracySec=1s
Persistent=true
Unit=rudderpi-ping-lan.service

[Install]
WantedBy=timers.target
"""

RUDDERPI_PROVISION_IP_SERVICE = """\
[Unit]
Description=Provision eth0 IP to Android phone (rudder-pi)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/rudder-pi/provision-phone-ip.sh
"""

RUDDERPI_PROVISION_IP_TIMER = """\
[Unit]
Description=Retry IP provisioning to phone

[Timer]
OnBootSec=15s
OnActiveSec=20s
AccuracySec=2s
Unit=rudderpi-provision-ip.service

[Install]
WantedBy=timers.target
"""


def run(cmd: list[str], *, check: bool = True, capture: bool = False, text: bool = True) -> subprocess.CompletedProcess:
    print(">>", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=capture, text=text)


def sudo_run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return run(["sudo", *cmd], check=check, capture=capture)


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


def write_text_atomic_as_root(path: Path, content: str) -> bool:
    """
    Write file as root, but only if content differs.
    Creates a one-time .bak copy of the original file.
    Returns True if the file was changed.
    """
    existing = ""
    if path.exists():
        existing = read_text(path)
        if existing == content:
            return False

    backup = Path(str(path) + ".bak")
    if path.exists() and not backup.exists():
        sudo_run(["cp", "-a", str(path), str(backup)])

    # Atomic-ish replace using tee (sufficient for small config/unit files).
    p = subprocess.Popen(["sudo", "tee", str(path)], stdin=subprocess.PIPE, text=True)
    assert p.stdin is not None
    p.stdin.write(content)
    p.stdin.close()
    rc = p.wait()
    if rc != 0:
        raise subprocess.CalledProcessError(rc, ["sudo", "tee", str(path)])

    return True


def systemd_daemon_reload() -> None:
    sudo_run(["systemctl", "daemon-reload"])


def systemd_enable_now(unit: str) -> None:
    sudo_run(["systemctl", "enable", "--now", unit])


def ensure_systemd_unit(unit_name: str, content: str) -> bool:
    """
    Ensure /etc/systemd/system/<unit_name> matches content.
    Returns True if changed.
    """
    unit_path = SYSTEMD_DIR / unit_name
    changed = write_text_atomic_as_root(unit_path, content.rstrip() + "\n")
    if changed:
        print(f"✅ Installed/updated {unit_path}")
    else:
        print(f"✅ {unit_path} already up-to-date")
    return changed


def ensure_default_target_multi_user() -> bool:
    """
    Ensure the default systemd target is multi-user.target (no GUI by default).
    Returns True if changed.
    """
    current = run(["systemctl", "get-default"], capture=True).stdout.strip()
    if current == "multi-user.target":
        print("✅ Default systemd target already multi-user.target")
        return False

    print(f"🔧 Changing default systemd target: {current} -> multi-user.target")
    sudo_run(["systemctl", "set-default", "multi-user.target"])
    return True


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

    new_lines: list[str] = []
    for line in lines:
        if line.strip() == AUDIO_PARAM_LINE:
            new_lines.append("# " + line)
            changed = True
            reboot_recommended = True
        else:
            new_lines.append(line)

    new_content = "\n".join(new_lines).rstrip() + "\n"
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
        sudo_run(["bash", "-lc", f"echo {ch} > {shlex.quote(str(export_path))}"])
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
    changed = write_text_atomic_as_root(SYSTEMD_PWM_SERVICE_PATH, service_content.rstrip() + "\n")
    if changed:
        print(f"✅ Wrote systemd service: {SYSTEMD_PWM_SERVICE_PATH}")
    else:
        print(f"✅ Systemd service already up-to-date: {SYSTEMD_PWM_SERVICE_PATH}")

    systemd_daemon_reload()
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
    sudo_run(["apt-get", "install", "-y", *pkgs])
    return True


def ensure_hostname(target: str) -> bool:
    """
    Ensure the system hostname matches target and persists across reboots.

    Handles cloud-init setups by writing:
      - preserve_hostname: true
      - manage_etc_hosts: false

    Also updates:
      - /etc/hostname
      - /etc/hosts (127.0.1.1 entry)

    Returns True if any changes were made.
    """
    changed = False

    cloud_cfg_dir = Path("/etc/cloud/cloud.cfg.d")
    cloud_cfg_path = cloud_cfg_dir / "99-rudderpi-hostname.cfg"
    desired_cloud_cfg = "preserve_hostname: true\nmanage_etc_hosts: false\n"

    sudo_run(["mkdir", "-p", str(cloud_cfg_dir)])
    existing_cloud_cfg = read_text(cloud_cfg_path) if cloud_cfg_path.exists() else ""
    if existing_cloud_cfg != desired_cloud_cfg:
        write_text_atomic_as_root(cloud_cfg_path, desired_cloud_cfg)
        print(f"✅ Wrote cloud-init override: {cloud_cfg_path}")
        changed = True

    current = run(["hostnamectl", "--static"], check=True, capture=True).stdout.strip()
    if current != target:
        print(f"🔧 Changing hostname: '{current}' -> '{target}'")
        sudo_run(["hostnamectl", "set-hostname", target])
        changed = True
    else:
        print(f"✅ Hostname already '{target}'")

    hostname_path = Path("/etc/hostname")
    existing_hostname = read_text(hostname_path).strip() if hostname_path.exists() else ""
    if existing_hostname != target:
        write_text_atomic_as_root(hostname_path, target.strip() + "\n")
        print("✅ Updated /etc/hostname")
        changed = True

    hosts_path = Path("/etc/hosts")
    hosts = read_text(hosts_path) if hosts_path.exists() else ""
    lines = hosts.splitlines()

    new_lines: list[str] = []
    found_127_0_1_1 = False
    for line in lines:
        if line.strip().startswith("127.0.1.1"):
            found_127_0_1_1 = True
            new_lines.append(f"127.0.1.1\t{target}")
        else:
            new_lines.append(line)

    if not found_127_0_1_1:
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
    Returns True if changes were made (best-effort).
    """
    print("📦 Ensuring avahi-daemon is installed")
    apt_install(["avahi-daemon", "libnss-mdns"])

    print("✅ Enabling + starting avahi-daemon")
    sudo_run(["systemctl", "enable", "--now", "avahi-daemon"])
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


def _mediamtx_url() -> str:
    arch = _machine_arch_for_mediamtx()
    return (
        f"https://github.com/bluenviron/mediamtx/releases/download/"
        f"{MEDIAMTX_VERSION}/mediamtx_{MEDIAMTX_VERSION}_{arch}.tar.gz"
    )


def _download_mediamtx_tarball(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "mediamtx.tar.gz"
    url = _mediamtx_url()

    sudo_run(["bash", "-lc", f"curl -fL {shlex.quote(url)} -o {shlex.quote(str(out))}"])
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
    apt_ok = True
    try:
        sudo_run(["apt-get", "update"])
        sudo_run(["apt-get", "install", "-y", "--no-install-recommends", "mediamtx"])
    except subprocess.CalledProcessError:
        apt_ok = False

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

        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(path=extract_dir)

        extracted_bin = extract_dir / "mediamtx"
        extracted_cfg = extract_dir / "mediamtx.yml"

        if not extracted_bin.exists():
            raise RuntimeError("Downloaded tarball did not contain 'mediamtx' binary")

        sudo_run(["install", "-m", "0755", str(extracted_bin), str(MEDIAMTX_BIN)])
        print(f"✅ Installed MediaMTX binary to {MEDIAMTX_BIN}")

        sudo_run(["mkdir", "-p", str(MEDIAMTX_ETC_DIR)])
        if extracted_cfg.exists() and not MEDIAMTX_CONFIG.exists():
            sudo_run(["install", "-m", "0644", str(extracted_cfg), str(MEDIAMTX_CONFIG)])
            print(f"✅ Seeded MediaMTX config to {MEDIAMTX_CONFIG}")

    return True


def _replace_or_insert_top_level_block(
    existing: str,
    *,
    key: str,
    desired_block: str,
    insert_after_key: str | None = None,
) -> tuple[str, bool]:
    """
    Replace an existing top-level YAML block (e.g. 'paths:' or 'webrtcICEServers2:')
    with desired_block, or insert it if missing.

    Assumptions (good enough for mediamtx.yml):
    - Top-level keys start at column 0: r'^[A-Za-z0-9_]+:'
    - A block runs until the next top-level key or EOF.
    """
    block_re = re.compile(
        rf"(?ms)^(?P<block>{re.escape(key)}:\n.*?)(?=^[A-Za-z0-9_]+:\s*$|\Z)"
    )

    m = block_re.search(existing)
    if m:
        current_block = m.group("block")
        if current_block == desired_block:
            return existing, False
        new = existing[: m.start("block")] + desired_block + existing[m.end("block") :]
        return new, True

    if insert_after_key:
        anchor_re = re.compile(rf"(?m)^{re.escape(insert_after_key)}:\s*$")
        am = anchor_re.search(existing)
        if am:
            insert_pos = existing.find("\n", am.end()) + 1
            if insert_pos <= 0:
                insert_pos = am.end()
            sep = "" if existing[:insert_pos].endswith("\n\n") else "\n"
            new = existing[:insert_pos] + sep + desired_block + "\n" + existing[insert_pos:]
            return new, True

    sep = "" if existing.endswith("\n") else "\n"
    new = existing + sep + "\n" + desired_block
    return new, True


def ensure_mediamtx_config_paths() -> bool:
    sudo_run(["mkdir", "-p", str(MEDIAMTX_ETC_DIR)])

    existing = read_text(MEDIAMTX_CONFIG) if MEDIAMTX_CONFIG.exists() else ""
    new_content, changed = _replace_or_insert_top_level_block(
        existing,
        key="paths",
        desired_block=MEDIAMTX_PATHS_FRAGMENT,
        insert_after_key=None,
    )

    if changed:
        write_text_atomic_as_root(MEDIAMTX_CONFIG, new_content.rstrip() + "\n")
        print("✅ Patched MediaMTX config: paths")
    else:
        print("✅ MediaMTX paths already configured")

    return changed


def _count_top_level_key_occurrences(text: str, key: str) -> int:
    return len(re.findall(rf"(?m)^{re.escape(key)}:\s*$", text))


def _remove_all_but_first_top_level_block(text: str, key: str) -> tuple[str, bool]:
    """
    Remove duplicate top-level YAML blocks, keeping the first occurrence.
    Returns (new_text, changed).
    """
    # Find all block ranges
    block_re = re.compile(
        rf"(?ms)^(?P<block>{re.escape(key)}:\s*\n.*?)(?=^[A-Za-z0-9_]+:\s*$|\Z)"
    )

    matches = list(block_re.finditer(text))
    if len(matches) <= 1:
        return text, False

    # Keep the first, remove the rest from end to start to preserve indices
    new = text
    for m in reversed(matches[1:]):
        new = new[: m.start("block")] + new[m.end("block") :]
    return new, True


def ensure_mediamtx_config_webrtc_ice_servers() -> bool:
    sudo_run(["mkdir", "-p", str(MEDIAMTX_ETC_DIR)])

    existing = read_text(MEDIAMTX_CONFIG) if MEDIAMTX_CONFIG.exists() else ""

    # 1) Deduplicate if the key exists multiple times (prevents MediaMTX crash)
    deduped, dedup_changed = _remove_all_but_first_top_level_block(existing, "webrtcICEServers2")

    # 2) Replace or insert the (single) block with our desired content
    new_content, replaced_changed = _replace_or_insert_top_level_block(
        deduped,
        key="webrtcICEServers2",
        desired_block=MEDIAMTX_WEBRTC_ICE_FRAGMENT,
        insert_after_key=None,
    )

    changed = dedup_changed or replaced_changed
    if changed:
        write_text_atomic_as_root(MEDIAMTX_CONFIG, new_content.rstrip() + "\n")
        print("✅ Patched MediaMTX config: webrtcICEServers2 (deduplicated + ensured)")
    else:
        print("✅ MediaMTX webrtcICEServers2 already configured")

    return changed


def ensure_mediamtx_service() -> bool:
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
    changed = write_text_atomic_as_root(MEDIAMTX_SERVICE_PATH, service_content.rstrip() + "\n")
    if changed:
        print(f"✅ Wrote systemd service: {MEDIAMTX_SERVICE_PATH}")
        systemd_daemon_reload()

    sudo_run(["systemctl", "enable", "--now", MEDIAMTX_SERVICE_NAME])
    print("✅ Enabled + started mediamtx")
    return changed


def ensure_rudder_pi_app_service() -> bool:
    # Soft checks (helps troubleshooting when installed elsewhere than /opt/rudder-pi)
    if not Path("/opt/rudder-pi/app.py").exists():
        print("⚠️  /opt/rudder-pi/app.py not found. If you installed elsewhere, adjust the systemd unit paths.")
    if not Path("/opt/rudder-pi/.venv/bin/python").exists():
        print("⚠️  /opt/rudder-pi/.venv/bin/python not found. Ensure the venv exists under /opt/rudder-pi/.venv.")

    changed = ensure_systemd_unit("rudder-pi.service", RUDDER_PI_SERVICE)
    if changed:
        systemd_daemon_reload()
    systemd_enable_now("rudder-pi.service")
    return changed


def ensure_rudderpi_ping_lan_timer() -> bool:
    changed_svc = ensure_systemd_unit("rudderpi-ping-lan.service", RUDDERPI_PING_LAN_SERVICE)
    changed_tmr = ensure_systemd_unit("rudderpi-ping-lan.timer", RUDDERPI_PING_LAN_TIMER)
    if changed_svc or changed_tmr:
        systemd_daemon_reload()
    systemd_enable_now("rudderpi-ping-lan.timer")
    return changed_svc or changed_tmr


def ensure_rudderpi_provision_ip_timer() -> bool:
    if not Path("/opt/rudder-pi/provision-phone-ip.sh").exists():
        print("⚠️  /opt/rudder-pi/provision-phone-ip.sh not found. If you installed elsewhere, adjust the systemd unit paths.")

    changed_svc = ensure_systemd_unit("rudderpi-provision-ip.service", RUDDERPI_PROVISION_IP_SERVICE)
    changed_tmr = ensure_systemd_unit("rudderpi-provision-ip.timer", RUDDERPI_PROVISION_IP_TIMER)
    if changed_svc or changed_tmr:
        systemd_daemon_reload()
    systemd_enable_now("rudderpi-provision-ip.timer")
    return changed_svc or changed_tmr


def system_provision(install_pwm_service: bool, reboot: bool) -> None:
    reboot_recommended = False

    # 0) Default target: multi-user.target (no GUI)
    changed_target = ensure_default_target_multi_user()

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
    changed_mtx_install = ensure_mediamtx_installed()
    changed_mtx_cfg_paths = ensure_mediamtx_config_paths()
    changed_mtx_cfg_ice = ensure_mediamtx_config_webrtc_ice_servers()
    changed_mtx_svc = ensure_mediamtx_service()

    # 4) rudder-pi + helper services
    changed_app_svc = ensure_rudder_pi_app_service()
    changed_ping = ensure_rudderpi_ping_lan_timer()
    changed_prov = ensure_rudderpi_provision_ip_timer()

    any_changes = (
        changed_target or changed_host or changed_cfg or changed_export or changed_pwm_svc
        or changed_mtx_install or changed_mtx_cfg_paths or changed_mtx_cfg_ice or changed_mtx_svc
        or changed_app_svc or changed_ping or changed_prov
    )

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
        help="Apply Raspberry Pi system changes (hostname+avahi+mediamtx+config.txt + PWM export + services). Requires sudo.",
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