#!/usr/bin/env python3
"""
Gradio control UI for a model boat (mobile-first layout):
- Manual tab: MediaMTX WebRTC iframe, state.json live view, PWM0/PWM1 sliders stacked vertically
- Sliders are -100..+100 (intended for ESC throttle with reverse)
- Soft ramp ("slew") toward target values in the background
- Single-controller lock so only one browser can drive at a time

v0.2.1:
- Stream URL selection dropdown with presets + optional custom value
- Optional TURN settings section (UI-only placeholder; does not alter iframe WebRTC behavior yet)
"""

from __future__ import annotations

import os
import time
import json
import uuid
import threading
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List
import re
import subprocess

import requests
import gradio as gr

try:
    import pigpio
except Exception:
    pigpio = None  # type: ignore

# =========================
# v0.2.2 - Safety deadman
# =========================

# How often UI should ping (seconds) and how long we tolerate silence.
SAFETY_HB_INTERVAL_SEC = float(os.getenv("SAFETY_HB_INTERVAL_SEC", "0.3"))
SAFETY_HB_TIMEOUT_SEC  = float(os.getenv("SAFETY_HB_TIMEOUT_SEC", "1.2"))

# If True, enforce FULL STOP when UI heartbeat is lost while control is held.
SAFETY_DEADMAN_ENABLED = os.getenv("SAFETY_DEADMAN_ENABLED", "1") == "1"

# Safety heartbeat (separate from lock TTL)
SAFETY_LAST_SEEN: float = 0.0
SAFETY_MUTEX = threading.Lock()

# =========================
# Config knobs (ESC mapping)
# =========================

THROTTLE_MIN = -100
THROTTLE_MAX = 100

ESC_PULSE_MIN_US = 1000
ESC_PULSE_CENTER_US = 1400
ESC_PULSE_MAX_US = 1800

RAMP_TICK_HZ = 50.0
MAX_DELTA_PER_TICK = 3.0

# =========================
# v0.2.1 - Mixer & safety
# =========================

# Neutral hold when switching forward <-> reverse (gas sign flip).
# 0.25..0.6s is usually a good range for car ESCs.
NEUTRAL_HOLD_SEC = 1.0

# Ignore tiny jitter around zero so we don't trigger a hold accidentally.
GAS_DEADBAND = 3.0  # throttle units (-100..+100)

# Output mapping / inversion
SWAP_OUTPUTS = False      # True => PWM0 <-> PWM1
INVERT_GAS = False        # invert gas input
INVERT_STEER = False      # invert steering input
INVERT_LEFT = False       # invert left motor output
INVERT_RIGHT = False      # invert right motor output

DEFAULT_STREAM_URL = "https://rudder-pi-webrtc.schrottplatz.internal/rudderpiraw"

# v0.2.1 stream presets
STREAM_PRESETS: List[str] = [
    "http://localhost:8889/rudderpi",
    "http://192.168.5.10:8889/rudderpi",
    "https://rudder-pi-webrtc.schrottplatz.internal/rudderpi",
    "https://rudder-pi-webrtc.schrottplatz.internal/rudderpiraw",
]

# =========================
# determine state json url
# =========================

def get_default_gateway(dev: str = "eth0") -> str | None:
    """
    Returns the IPv4 default gateway for the given interface, e.g. '192.168.153.1'.
    """
    try:
        out = subprocess.check_output(
            ["ip", "-4", "route", "show", "default", "dev", dev],
            text=True,
        ).strip()
        # Example: "default via 192.168.153.1 dev eth0 proto dhcp src 192.168.153.146 metric 202"
        m = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)\b", out)
        return m.group(1) if m else None
    except Exception:
        return None

def guess_state_url(dev: str = "eth0") -> str | None:
    gw = get_default_gateway(dev)
    if not gw:
        return None
    return f"http://{gw}:8080/state.json"

# state.json url
STATE_URL = os.getenv("STATE_URL", "").strip()
if not STATE_URL:
    guessed = guess_state_url("eth0")
    if guessed:
        STATE_URL = guessed
    else:
        STATE_URL = "http://192.168.42.129/state.json" # fallback



# =========================
# Backend abstractions
# =========================

class PWMBackend:
    """Abstract PWM backend (ESC style)."""
    def set_throttle(self, channel: int, value: float) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass


class MockPWMBackend(PWMBackend):
    """Mock backend for WSL/testing."""
    def __init__(self) -> None:
        self.last_throttle: Dict[int, float] = {}
        self.last_pulse_us: Dict[int, int] = {}

    def set_throttle(self, channel: int, value: float) -> None:
        self.last_throttle[channel] = float(value)
        self.last_pulse_us[channel] = throttle_to_pulse_us(value)


class PigpioServoBackend(PWMBackend):
    """
    ESC pulses using pigpio DMA timing (stable).
    pigpio expects pulse width in microseconds.
    """
    def __init__(self, gpio_map: dict[int, int]) -> None:
        if pigpio is None:
            raise RuntimeError("pigpio is not installed.")
        self.gpio_map = gpio_map
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("pigpio daemon not running (pigpiod).")
        for gpio_num in self.gpio_map.values():
            self.pi.set_mode(gpio_num, pigpio.OUTPUT)
            self.pi.set_servo_pulsewidth(gpio_num, 0)  # stop

    def set_throttle(self, channel: int, value: float) -> None:
        gpio_num = self.gpio_map[channel]
        pulse_us = throttle_to_pulse_us(value)
        self.pi.set_servo_pulsewidth(gpio_num, int(pulse_us))

    def stop(self) -> None:
        for gpio_num in self.gpio_map.values():
            self.pi.set_servo_pulsewidth(gpio_num, 0)
        self.pi.stop()


@dataclass
class SysfsPwmChannel:
    chip_path: str  # e.g. /sys/class/pwm/pwmchip0
    index: int      # 0 or 1

    @property
    def path(self) -> str:
        return os.path.join(self.chip_path, f"pwm{self.index}")

    def _write(self, name: str, value: str) -> None:
        with open(os.path.join(self.path, name), "w", encoding="utf-8") as f:
            f.write(value)

    def _read(self, name: str) -> str:
        with open(os.path.join(self.path, name), "r", encoding="utf-8") as f:
            return f.read().strip()

    def ensure_exported(self) -> None:
        if os.path.isdir(self.path):
            return
        export_path = os.path.join(self.chip_path, "export")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(str(self.index))
        # Wait a tiny bit for sysfs to create pwmN directory
        for _ in range(50):
            if os.path.isdir(self.path):
                return
            time.sleep(0.01)
        raise RuntimeError(f"PWM channel pwm{self.index} did not appear under sysfs.")

    def disable(self) -> None:
        self._write("enable", "0")

    def enable(self) -> None:
        self._write("enable", "1")

    def set_period_ns(self, period_ns: int) -> None:
        # Many drivers require disabling before period change
        self.disable()
        self._write("period", str(period_ns))

    def set_duty_ns(self, duty_ns: int) -> None:
        self._write("duty_cycle", str(duty_ns))


class SysfsEscBackend(PWMBackend):
    """
    ESC-style signal via sysfs PWM.
    - Fixed 50 Hz (20 ms period)
    - UI provides signed throttle -100..+100
    """
    def __init__(
        self,
        chip_path: str = "/sys/class/pwm/pwmchip0",
        channel_to_index: dict[int, int] = {0: 0, 1: 1},
        period_ns: int = 20_000_000,
        min_us: int = 1000,
        neutral_us: int = 1400,
        max_us: int = 1800,
    ) -> None:
        self.chip_path = chip_path
        self.period_ns = int(period_ns)
        self.min_us = int(min_us)
        self.neutral_us = int(neutral_us)
        self.max_us = int(max_us)

        self.channels: dict[int, SysfsPwmChannel] = {}

        for logical_ch, idx in channel_to_index.items():
            ch = SysfsPwmChannel(chip_path, idx)
            ch.ensure_exported()
            ch.set_period_ns(self.period_ns)
            ch.set_duty_ns(self._us_to_ns(self.neutral_us))
            ch.enable()
            self.channels[logical_ch] = ch

    @staticmethod
    def _us_to_ns(us: int) -> int:
        return int(us) * 1000

    def set_throttle(self, channel: int, value: float) -> None:
        """
        value: -100 .. +100
        """
        if channel not in self.channels:
            raise ValueError(f"Unknown PWM channel: {channel}")

        x = max(-100.0, min(100.0, float(value)))

        if x >= 0:
            us = self.neutral_us + (self.max_us - self.neutral_us) * (x / 100.0)
        else:
            us = self.neutral_us + (self.neutral_us - self.min_us) * (x / 100.0)

        duty_ns = self._us_to_ns(int(round(us)))

        if duty_ns < 0:
            duty_ns = 0
        if duty_ns > self.period_ns:
            duty_ns = self.period_ns

        self.channels[channel].set_duty_ns(duty_ns)

    def stop(self) -> None:
        for ch in self.channels.values():
            ch.set_duty_ns(self._us_to_ns(self.neutral_us))
            ch.disable()


def build_pwm_backend() -> PWMBackend:
    is_pi = os.path.exists("/proc/device-tree/model")

    if is_pi and os.path.isdir("/sys/class/pwm/pwmchip0"):
        return SysfsEscBackend(
            chip_path="/sys/class/pwm/pwmchip0",
            channel_to_index={0: 0, 1: 1},
            period_ns=20_000_000,
            min_us=1000,
            neutral_us=1400,
            max_us=1800,
        )

    return MockPWMBackend()


def throttle_to_pulse_us(throttle: float) -> int:
    """
    Map throttle -100..+100 to pulse width:
      -100 => ESC_PULSE_MIN_US
        0  => ESC_PULSE_CENTER_US
      +100 => ESC_PULSE_MAX_US
    """
    t = float(throttle)
    t = max(float(THROTTLE_MIN), min(float(THROTTLE_MAX), t))

    if t >= 0:
        span = ESC_PULSE_MAX_US - ESC_PULSE_CENTER_US
        pulse = ESC_PULSE_CENTER_US + (t / 100.0) * span
    else:
        span = ESC_PULSE_CENTER_US - ESC_PULSE_MIN_US
        pulse = ESC_PULSE_CENTER_US + (t / 100.0) * span
    return int(round(pulse))


# =========================
# Single-controller lock
# =========================

@dataclass
class ControlLock:
    controller_id: Optional[str] = None
    controller_name: Optional[str] = None
    last_seen: float = 0.0
    ttl_sec: int = 20

    def is_held(self) -> bool:
        if not self.controller_id:
            return False
        return (time.time() - self.last_seen) <= self.ttl_sec

    def holder_label(self) -> str:
        if self.is_held():
            return f"{self.controller_name or 'Unbekannt'} ({self.controller_id[:8]})"
        return "niemand"


LOCK = ControlLock()
LOCK_MUTEX = threading.Lock()

def full_stop_now(reason: str = "deadman") -> None:
    """
    Immediately neutralize motors (bypasses ramp as much as possible).
    """
    global _hold_until_ts

    # Neutral targets
    with PWM_MUTEX:
        global GAS_TARGET, STEER_TARGET
        GAS_TARGET = 0.0
        STEER_TARGET = 0.0
        MOTOR_TARGET[0] = 0.0
        MOTOR_TARGET[1] = 0.0
        MOTOR_CURRENT[0] = 0.0
        MOTOR_CURRENT[1] = 0.0
        _hold_until_ts = 0.0

    # Apply neutral immediately to outputs
    try:
        out0 = _map_output_channel(0)
        out1 = _map_output_channel(1)
        PWM.set_throttle(out0, 0.0)
        PWM.set_throttle(out1, 0.0)
    except Exception:
        pass


def ensure_lock_cleanup() -> None:
    with LOCK_MUTEX:
        if LOCK.controller_id and not LOCK.is_held():
            LOCK.controller_id = None
            LOCK.controller_name = None
            LOCK.last_seen = 0.0


def take_control(session_id: str, name: str) -> Tuple[bool, str]:
    ensure_lock_cleanup()
    with LOCK_MUTEX:
        if LOCK.controller_id is None:
            LOCK.controller_id = session_id
            LOCK.controller_name = name.strip()[:32] or "Controller"
            LOCK.last_seen = time.time()
            return True, f"✅ Control übernommen: {LOCK.holder_label()}"
        if LOCK.controller_id == session_id:
            LOCK.last_seen = time.time()
            return True, f"✅ Du hast bereits Control: {LOCK.holder_label()}"
        return False, f"⛔ Control ist belegt von: {LOCK.holder_label()}"


def release_control(session_id: str) -> str:
    with LOCK_MUTEX:
        if LOCK.controller_id == session_id:
            LOCK.controller_id = None
            LOCK.controller_name = None
            LOCK.last_seen = 0.0
            return "✅ Control freigegeben."
    return "ℹ️ Du hattest kein Control."


def heartbeat(session_id: str) -> str:
    ensure_lock_cleanup()
    now = time.time()
    with LOCK_MUTEX:
        if LOCK.controller_id == session_id:
            LOCK.last_seen = now

            # Safety: mark UI alive
            with SAFETY_MUTEX:
                global SAFETY_LAST_SEEN
                SAFETY_LAST_SEEN = now

            return f"🟢 Control aktiv: {LOCK.holder_label()}"
        return f"🔒 Control belegt von: {LOCK.holder_label()}"


def can_control(session_id: str) -> bool:
    ensure_lock_cleanup()
    with LOCK_MUTEX:
        return LOCK.controller_id == session_id and LOCK.is_held()


# =========================
# state.json polling
# =========================

STATE_CACHE: Dict[str, Any] = {"ok": False, "ts": 0.0, "data": None, "error": "not loaded"}
STATE_MUTEX = threading.Lock()

STATE_CFG_MUTEX = threading.Lock()
STATE_XAUTH = "rudderpi"

def poll_state_forever(interval_sec: float = 1.0) -> None:
    while True:
        try:
            with STATE_CFG_MUTEX:
                url = STATE_URL
                pw = STATE_XAUTH

            if not url:
                raise ValueError("state.json URL is empty")
            headers = {"X-Auth": pw} if pw else {}
            r = requests.get(url, headers=headers, timeout=1.5)
            r.raise_for_status()
            data = r.json()
            payload = {"ok": True, "ts": time.time(), "data": data, "error": None}
        except Exception as e:
            payload = {"ok": False, "ts": time.time(), "data": None, "error": str(e)}

        with STATE_MUTEX:
            STATE_CACHE.update(payload)
        time.sleep(interval_sec)


def get_state_pretty() -> str:
    with STATE_MUTEX:
        snap = dict(STATE_CACHE)
    stamp = time.strftime("%H:%M:%S", time.localtime(snap.get("ts", 0)))
    if snap.get("ok"):
        return f"Zeit: {stamp}\n\n" + json.dumps(snap["data"], indent=2, ensure_ascii=False)
    return f"Zeit: {stamp}\n\nERROR: {snap.get('error')}"

def derive_base_url_from_state_url(state_url: str) -> str:
    url = (state_url or "").strip()
    if not url:
        return ""
    if url.endswith("/state.json"):
        return url[:-len("/state.json")]
    return url.rstrip("/")


def torch_action(state_url: str, pw: str, turn_on: bool) -> str:
    try:
        base_url = derive_base_url_from_state_url(state_url)
        if not base_url:
            return "⚠️ Keine gültige State URL gesetzt."

        endpoint = f"{base_url}/torch/on" if turn_on else f"{base_url}/torch/off"
        headers = {"X-Auth": pw} if pw else {}

        r = requests.post(endpoint, headers=headers, timeout=3.0)
        r.raise_for_status()

        try:
            data = r.json()
        except Exception:
            return f"⚠️ Torch-Request ok, aber Antwort war kein JSON ({r.status_code})."

        if data.get("ok"):
            return "🔦 Torch eingeschaltet." if turn_on else "🌑 Torch ausgeschaltet."
        return f"⚠️ Torch-Request fehlgeschlagen: {data}"

    except Exception as e:
        return f"ERROR Torch: {e}"

# =========================
# Soft-ramp PWM controller
# =========================

PWM = build_pwm_backend()

PWM_MUTEX = threading.Lock()
GAS_TARGET = 0.0
STEER_TARGET = 0.0

MOTOR_TARGET = {0: 0.0, 1: 0.0}   # logical channels 0/1
MOTOR_CURRENT = {0: 0.0, 1: 0.0}
PWM_LAST_APPLIED = {0: None, 1: None}

_hold_until_ts = 0.0
_last_gas_sign = 0  # -1,0,+1


def _slew_step(current: float, target: float, max_delta: float) -> float:
    if current < target:
        return min(current + max_delta, target)
    if current > target:
        return max(current - max_delta, target)
    return current

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _sign_with_deadband(x: float, deadband: float) -> int:
    if x > deadband:
        return 1
    if x < -deadband:
        return -1
    return 0

def _apply_inversion(x: float, inv: bool) -> float:
    return -x if inv else x

def _mix_gas_steer(gas: float, steer: float) -> Tuple[float, float]:
    left = gas + steer
    right = gas - steer
    left = _clamp(left, THROTTLE_MIN, THROTTLE_MAX)
    right = _clamp(right, THROTTLE_MIN, THROTTLE_MAX)
    left = _apply_inversion(left, INVERT_LEFT)
    right = _apply_inversion(right, INVERT_RIGHT)
    return left, right

def _map_output_channel(logical_ch: int) -> int:
    if not SWAP_OUTPUTS:
        return logical_ch
    return 1 if logical_ch == 0 else 0


_worker_started = False
_worker_mutex = threading.Lock()

def ensure_pwm_worker() -> None:
    global _worker_started
    with _worker_mutex:
        if _worker_started:
            return
        t = threading.Thread(target=pwm_worker_forever, daemon=True)
        t.start()
        _worker_started = True

_safety_started = False
_safety_mutex = threading.Lock()

def ensure_safety_watchdog() -> None:
    global _safety_started
    with _safety_mutex:
        if _safety_started:
            return
        t = threading.Thread(target=safety_watchdog_forever, daemon=True)
        t.start()
        _safety_started = True

def safety_watchdog_forever() -> None:
    global SAFETY_LAST_SEEN
    while True:
        time.sleep(0.1)

        if not SAFETY_DEADMAN_ENABLED:
            continue

        ensure_lock_cleanup()

        with LOCK_MUTEX:
            held = LOCK.is_held()
            holder = LOCK.controller_id

        if not held or not holder:
            continue  # nobody actively controls

        with SAFETY_MUTEX:
            last = SAFETY_LAST_SEEN

        if last <= 0:
            continue

        if (time.time() - last) > SAFETY_HB_TIMEOUT_SEC:
            full_stop_now("ui_heartbeat_lost")
            # Reset so we don't spam-stop; UI must heartbeat again.
            with SAFETY_MUTEX:
                SAFETY_LAST_SEEN = 0.0


def pwm_worker_forever() -> None:
    global _hold_until_ts, _last_gas_sign

    tick = 1.0 / RAMP_TICK_HZ
    while True:
        time.sleep(tick)
        now = time.time()

        with PWM_MUTEX:
            gas = float(GAS_TARGET)
            steer = float(STEER_TARGET)

        gas = _apply_inversion(gas, INVERT_GAS)
        steer = _apply_inversion(steer, INVERT_STEER)

        gas_sign = _sign_with_deadband(gas, GAS_DEADBAND)
        if gas_sign != 0 and _last_gas_sign != 0 and gas_sign != _last_gas_sign:
            _hold_until_ts = now + float(NEUTRAL_HOLD_SEC)

        if now < _hold_until_ts:
            left_t, right_t = 0.0, 0.0
        else:
            left_t, right_t = _mix_gas_steer(gas, steer)

        _last_gas_sign = gas_sign if gas_sign != 0 else _last_gas_sign

        with PWM_MUTEX:
            MOTOR_TARGET[0] = left_t
            MOTOR_TARGET[1] = right_t
            c0, c1 = MOTOR_CURRENT[0], MOTOR_CURRENT[1]
            t0, t1 = MOTOR_TARGET[0], MOTOR_TARGET[1]

        n0 = _slew_step(c0, t0, MAX_DELTA_PER_TICK)
        n1 = _slew_step(c1, t1, MAX_DELTA_PER_TICK)

        out0 = _map_output_channel(0)
        out1 = _map_output_channel(1)

        PWM.set_throttle(out0, n0)
        PWM.set_throttle(out1, n1)

        with PWM_MUTEX:
            MOTOR_CURRENT[0] = n0
            MOTOR_CURRENT[1] = n1
            PWM_LAST_APPLIED[out0] = throttle_to_pulse_us(n0)
            PWM_LAST_APPLIED[out1] = throttle_to_pulse_us(n1)


def set_targets_from_ui(session_id: str, gas: float, steer: float) -> str:
    ensure_pwm_worker()

    if not can_control(session_id):
        return "⛔ Du hast kein Control. Werte werden nicht angewendet."

    g = float(_clamp(gas, THROTTLE_MIN, THROTTLE_MAX))
    s = float(_clamp(steer, THROTTLE_MIN, THROTTLE_MAX))

    with PWM_MUTEX:
        global GAS_TARGET, STEER_TARGET
        GAS_TARGET = g
        STEER_TARGET = s

        out0 = _map_output_channel(0)
        out1 = _map_output_channel(1)
        p0 = PWM_LAST_APPLIED[out0]
        p1 = PWM_LAST_APPLIED[out1]

    return f"✅ Target: Gas={g:.0f}, Lenk={s:.0f} | aktuell: {p0}us / {p1}us"


def pwm_stop(session_id: str) -> str:
    if not can_control(session_id):
        return "⛔ Du hast kein Control."
    with PWM_MUTEX:
        global GAS_TARGET, STEER_TARGET
        GAS_TARGET = 0.0
        STEER_TARGET = 0.0
    return "🛑 Stop: Gas/Lenk auf 0 gesetzt (soft ramp zurück zur Mitte)."


# =========================
# System actions
# =========================

def system_action(action: str) -> str:
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "0") != "1":
        return "⚠️ System-Aktionen sind deaktiviert. Setze ENABLE_SYSTEM_ACTIONS=1 um zu aktivieren."

    if action == "reboot":
        cmd = ["sudo", "systemctl", "reboot"]
    elif action == "shutdown":
        cmd = ["sudo", "systemctl", "poweroff"]
    else:
        return "Unknown action."

    try:
        subprocess.Popen(cmd)
        return f"✅ {action} ausgelöst."
    except Exception as e:
        return f"ERROR: {e}"

# =========================
# Gradio UI (mobile-first)
# =========================

MOBILE_CSS = """
.rp-phone {
  max-width: 420px;
  margin: 0 auto;
  padding: 10px;
}
.rp-phone button, .rp-phone input, .rp-phone textarea {
  font-size: 16px;
}
.rp-video {
  width: 100%;
  aspect-ratio: 4 / 3;
  border: 0;
  border-radius: 14px;
  overflow: hidden;
}
.rp-video iframe {
  width: 100%;
  height: 100%;
  border: 0;
  border-radius: 14px;
}
.block.svelte-1ed2p3z { padding-top: 8px; }
"""

def make_session_id() -> str:
    return str(uuid.uuid4())

def build_webrtc_iframe(url: str) -> str:
    safe_url = (url or "").strip()
    if not safe_url:
        return "<div style='padding:12px'>Kein Stream-URL gesetzt.</div>"
    return f"""
    <div class="rp-video">
      <iframe
        src="{safe_url}"
        allow="camera; microphone; autoplay; encrypted-media; fullscreen; picture-in-picture"
      ></iframe>
    </div>
    """

_poller_started = False
_poller_mutex = threading.Lock()

def ensure_state_poller() -> None:
    global _poller_started
    with _poller_mutex:
        if _poller_started:
            return
        t = threading.Thread(target=poll_state_forever, args=(1.0,), daemon=True)
        t.start()
        _poller_started = True


def app() -> gr.Blocks:
    with gr.Blocks(title="RudderPi Control", css=MOBILE_CSS, theme=gr.themes.Soft()) as demo:
        session_id = gr.State(make_session_id)

        with gr.Column(elem_classes=["rp-phone"]):
            gr.Markdown("## RudderPi – Control UI")

            controller_name = gr.Textbox(label="Controller-Name", value="Browser", max_lines=1)
            lock_status = gr.Markdown("🔒 Control belegt von: niemand")
            with gr.Row():
                btn_take = gr.Button("Control übernehmen", variant="primary")
                btn_release = gr.Button("Control freigeben")

            hb_timer = gr.Timer(SAFETY_HB_INTERVAL_SEC)
            hb_out = gr.Textbox(visible=False)

            def on_take(sid: str, name: str) -> str:
                _, msg = take_control(sid, name)
                return msg

            def on_release(sid: str) -> str:
                return release_control(sid)

            def on_hb(sid: str) -> Tuple[str, str]:
                status = heartbeat(sid)
                return status, status

            btn_take.click(on_take, inputs=[session_id, controller_name], outputs=[lock_status])
            btn_release.click(on_release, inputs=[session_id], outputs=[lock_status])
            demo.load(on_hb, inputs=[session_id], outputs=[lock_status, hb_out])
            hb_timer.tick(on_hb, inputs=[session_id], outputs=[lock_status, hb_out])

            with gr.Tabs():
                with gr.Tab("Manual"):
                    gr.Markdown("### Video")

                    # v0.2.1: Stream selection preset dropdown + optional custom
                    stream_choice = gr.Dropdown(
                        label="Video endpoint (preset)",
                        choices=STREAM_PRESETS,
                        value=DEFAULT_STREAM_URL if DEFAULT_STREAM_URL in STREAM_PRESETS else STREAM_PRESETS[-1],
                        allow_custom_value=False,
                    )
                    stream_custom = gr.Textbox(
                        label="Custom endpoint (optional)",
                        value="",
                        max_lines=1,
                        placeholder="e.g. https://example/rudderpi (leave empty to use preset)",
                    )

                    def resolve_stream_url(preset: str, custom: str) -> Tuple[str, str]:
                        url = (custom or "").strip() or (preset or "").strip()
                        return url, build_webrtc_iframe(url)

                    stream_view = gr.HTML(build_webrtc_iframe(DEFAULT_STREAM_URL))

                    # Update iframe when preset/custom changes
                    stream_choice.change(resolve_stream_url, inputs=[stream_choice, stream_custom], outputs=[gr.State(), stream_view])
                    stream_custom.change(resolve_stream_url, inputs=[stream_choice, stream_custom], outputs=[gr.State(), stream_view])

                    # PWM sliders stacked vertically (mobile-first)
                    gr.Markdown("### Motorregler (ESC)")

                    gas = gr.Slider(THROTTLE_MIN, THROTTLE_MAX, value=0, step=1, label="Gas (−100 .. +100)")
                    steer = gr.Slider(THROTTLE_MIN, THROTTLE_MAX, value=0, step=1, label="Lenkung (−100 .. +100)")

                    with gr.Row():
                        btn_stop = gr.Button("🛑 Stop (0)", variant="stop")
                    pwm_status = gr.Markdown("")

                    gas.change(set_targets_from_ui, inputs=[session_id, gas, steer], outputs=[pwm_status])
                    steer.change(set_targets_from_ui, inputs=[session_id, gas, steer], outputs=[pwm_status])
                    btn_stop.click(pwm_stop, inputs=[session_id], outputs=[pwm_status])

 

                    # Live state.json
                    gr.Markdown("### state.json (live)")
                    state_url = gr.Textbox(label="State URL", value=STATE_URL, max_lines=1)
                    btn_detect = gr.Button("Auto-detect from eth0")
                    def detect_state_url() -> str:
                        url = guess_state_url("eth0")
                        return url or ""
                    btn_detect.click(detect_state_url, outputs=[state_url])
                    
                    xauth_pw = gr.Textbox(label="X-Auth Password", value=STATE_XAUTH, type="password", max_lines=1)
                    state_text = gr.Code(label="state.json", language="json")

                    def on_state_cfg_change(url: str, pw: str) -> str:
                        global STATE_URL, STATE_XAUTH
                        with STATE_CFG_MUTEX:
                            STATE_URL = (url or "").strip()
                            STATE_XAUTH = pw or ""
                        ensure_state_poller()
                        return get_state_pretty()

                    state_url.change(on_state_cfg_change, inputs=[state_url, xauth_pw], outputs=[state_text])
                    xauth_pw.change(on_state_cfg_change, inputs=[state_url, xauth_pw], outputs=[state_text])

                    state_timer = gr.Timer(1.0)

                    def on_load() -> str:
                        ensure_state_poller()
                        ensure_pwm_worker()
                        ensure_safety_watchdog()
                        return get_state_pretty()

                    demo.load(on_load, outputs=[state_text])
                    state_timer.tick(lambda: get_state_pretty(), outputs=[state_text])
                    
                    gr.Markdown("### Torch")

                    with gr.Row():
                        btn_torch_on = gr.Button("🔦 Torch ON")
                        btn_torch_off = gr.Button("🌑 Torch OFF")

                    torch_status = gr.Markdown("")
                    
                    btn_torch_on.click(
                        lambda url, pw: torch_action(url, pw, True),
                        inputs=[state_url, xauth_pw],
                        outputs=[torch_status],
                    )

                    btn_torch_off.click(
                        lambda url, pw: torch_action(url, pw, False),
                        inputs=[state_url, xauth_pw],
                        outputs=[torch_status],
                    )

                with gr.Tab("Auto"):
                    gr.Markdown("Hier kommt später der Autopilot rein (Heading-Hold, Waypoints, Safety, Logs).")

                with gr.Tab("System"):
                    gr.Markdown("### System Actions (standardmäßig deaktiviert)")
                    gr.Markdown("Aktiviere mit `ENABLE_SYSTEM_ACTIONS=1` (und sudo-Rechte am Pi).")
                    with gr.Row():
                        btn_reboot = gr.Button("🔄 Reboot")
                        btn_shutdown = gr.Button("⏻ Shutdown", variant="stop")
                    sys_out = gr.Markdown()
                    btn_reboot.click(lambda: system_action("reboot"), outputs=[sys_out])
                    btn_shutdown.click(lambda: system_action("shutdown"), outputs=[sys_out])

    return demo


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "7860"))
    app().launch(server_name=host, server_port=port)
