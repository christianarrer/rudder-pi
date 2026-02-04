#!/usr/bin/env python3
"""
Gradio control UI for a model boat:
- Tabs: Manual / Auto / System
- Manual: MediaMTX WebRTC iframe, state.json polling with X-Auth, PWM0/PWM1 sliders
- Enforces single-controller lock (only one browser can send control commands)
- Safe by default: system shutdown/reboot disabled unless ENABLE_SYSTEM_ACTIONS=1

Test on WSL: PWM backend is mocked automatically.
On Raspberry Pi: implement real PWM backend (pigpio recommended) later.
"""

from __future__ import annotations

import os
import time
import json
import uuid
import threading
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

import requests
import gradio as gr
import pigpio
import os
from dataclasses import dataclass


# =========================
# Backend abstractions
# =========================

class PWMBackend:
    """Abstract PWM backend."""
    def set_pwm(self, channel: int, duty_cycle: float, frequency_hz: int) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass


class MockPWMBackend(PWMBackend):
    """Mock backend for WSL/testing."""
    def __init__(self) -> None:
        self.last: Dict[int, Tuple[float, int]] = {}

    def set_pwm(self, channel: int, duty_cycle: float, frequency_hz: int) -> None:
        self.last[channel] = (duty_cycle, frequency_hz)
        # Intentionally no print spam; UI can display last values.


class PigpioServoBackend(PWMBackend):
    """
    Servo-style pulses for ESCs:
    - Use pigpio DMA timing, works on any GPIO, very stable.
    - Typical ESC expects ~50Hz, pulse width 1000..2000us.
    """
    def __init__(self, gpio_map: dict[int, int]) -> None:
        self.gpio_map = gpio_map
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("pigpio daemon not running (pigpiod).")
        # Optional: set pin modes
        for gpio in self.gpio_map.values():
            self.pi.set_mode(gpio, pigpio.OUTPUT)
            self.pi.set_servo_pulsewidth(gpio, 0)  # stop

    def set_pwm(self, channel: int, duty_cycle: float, frequency_hz: int) -> None:
        # Interpret duty_cycle as "throttle %" for ESC: 0..100 => 1000..2000us
        # You can later change mapping to -100..+100 if you want reverse etc.
        gpio = self.gpio_map[channel]
        duty_cycle = max(0.0, min(100.0, float(duty_cycle)))
        pulse_us = int(1000 + (duty_cycle / 100.0) * 1000)  # 1000..2000us
        self.pi.set_servo_pulsewidth(gpio, pulse_us)

    def stop(self) -> None:
        for gpio in self.gpio_map.values():
            self.pi.set_servo_pulsewidth(gpio, 0)
        self.pi.stop()

@dataclass
class SysfsPwmChannel:
    """
    Thin sysfs PWM helper.

    Paths:
      chip_path: /sys/class/pwm/pwmchip0
      channel path: /sys/class/pwm/pwmchip0/pwm0 (or pwm1)

    Notes:
    - Some drivers require disabling before changing period (and sometimes duty_cycle).
    - duty_cycle must never exceed period.
    """
    chip_path: str  # e.g. /sys/class/pwm/pwmchip0
    index: int      # 0 or 1

    @property
    def path(self) -> str:
        return os.path.join(self.chip_path, f"pwm{self.index}")

    def _write_chip(self, name: str, value: str) -> None:
        with open(os.path.join(self.chip_path, name), "w", encoding="utf-8") as f:
            f.write(value)

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
        # Write index to export
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(str(self.index))

        # Wait a bit for sysfs to create pwmN directory
        for _ in range(50):
            if os.path.isdir(self.path):
                return
            time.sleep(0.01)

        raise RuntimeError(f"PWM channel pwm{self.index} did not appear under sysfs: {self.path}")

    def disable(self) -> None:
        # Some drivers error if enable doesn't exist yet; ensure exported first.
        self.ensure_exported()
        self._write("enable", "0")

    def enable(self) -> None:
        self.ensure_exported()
        self._write("enable", "1")

    def set_period_ns(self, period_ns: int) -> None:
        """
        Many drivers require disabling before period change.
        """
        self.ensure_exported()
        # Disable before touching period
        try:
            self._write("enable", "0")
        except Exception:
            # If enable isn't writable yet, ignore and try writing period anyway
            pass
        self._write("period", str(int(period_ns)))

    def set_duty_ns(self, duty_ns: int) -> None:
        """
        duty_cycle must be within [0, period].
        Some drivers are happier if duty_cycle is changed while disabled,
        but not all require it. We keep it simple and only clamp.
        """
        self.ensure_exported()
        duty = int(duty_ns)

        # Clamp to current period if readable
        try:
            period = int(self._read("period"))
            if duty < 0:
                duty = 0
            if duty > period:
                duty = period
        except Exception:
            # If period read fails, just guard non-negative
            if duty < 0:
                duty = 0

        self._write("duty_cycle", str(duty))


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
            import time
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
    ESC/servo-style signal via sysfs PWM.
    - Fixed 50 Hz (20 ms period)
    - duty_cycle expresses pulse width (e.g. 1000..2000 us)
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
            # Start at neutral (safe stop for many ESCs)
            ch.set_duty_ns(self._us_to_ns(self.neutral_us))
            ch.enable()
            self.channels[logical_ch] = ch

    @staticmethod
    def _us_to_ns(us: int) -> int:
        return int(us) * 1000

    def set_pwm(self, channel: int, duty_cycle: float, frequency_hz: int) -> None:
        """
        Interpret 'duty_cycle' from UI as a signed throttle percentage:
        -100..+100 mapped around neutral pulse width.
        """
        if channel not in self.channels:
            raise ValueError(f"Unknown PWM channel: {channel}")

        # Clamp input
        x = float(duty_cycle)
        if x < -100.0:
            x = -100.0
        if x > 100.0:
            x = 100.0

        # Map percentage to pulse width
        if x >= 0:
            # neutral -> max
            us = self.neutral_us + (self.max_us - self.neutral_us) * (x / 100.0)
        else:
            # neutral -> min
            us = self.neutral_us + (self.neutral_us - self.min_us) * (x / 100.0)  # x negative

        us_i = int(round(us))
        duty_ns = self._us_to_ns(us_i)

        # Safety: never exceed period
        if duty_ns < 0:
            duty_ns = 0
        if duty_ns > self.period_ns:
            duty_ns = self.period_ns

        self.channels[channel].set_duty_ns(duty_ns)

    def stop(self) -> None:
        # Set neutral and disable outputs
        for ch in self.channels.values():
            ch.set_duty_ns(self._us_to_ns(self.neutral_us))
            ch.disable()

def build_pwm_backend() -> PWMBackend:
    is_pi = os.path.exists("/proc/device-tree/model")
    if is_pi and os.path.isdir("/sys/class/pwm/pwmchip0"):
        # You configured dtoverlay=pwm-2chan,pin=12...,pin2=13...
        # pwm0 -> GPIO12 (pin 32), pwm1 -> GPIO13 (pin 33)
        return SysfsEscBackend(
            chip_path="/sys/class/pwm/pwmchip0",
            channel_to_index={0: 0, 1: 1},
            period_ns=20_000_000,
            min_us=1000,
            neutral_us=1400,
            max_us=1800,
        )
    return MockPWMBackend()


# =========================
# Single-controller lock
# =========================

@dataclass
class ControlLock:
    controller_id: Optional[str] = None
    controller_name: Optional[str] = None
    last_seen: float = 0.0
    ttl_sec: int = 20  # If controller stops heartbeating, lock expires

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


def ensure_lock_cleanup() -> None:
    """Release lock if TTL expired."""
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
    with LOCK_MUTEX:
        if LOCK.controller_id == session_id:
            LOCK.last_seen = time.time()
            return f"🟢 Control aktiv: {LOCK.holder_label()}"
        return f"🔒 Control belegt von: {LOCK.holder_label()}"


def can_control(session_id: str) -> bool:
    ensure_lock_cleanup()
    with LOCK_MUTEX:
        return LOCK.controller_id == session_id and LOCK.is_held()


# =========================
# State polling (state.json)
# =========================

STATE_CACHE: Dict[str, Any] = {"ok": False, "ts": 0.0, "data": None, "error": "not loaded"}
STATE_MUTEX = threading.Lock()

def poll_state_forever(get_url_fn, get_pw_fn, interval_sec: float = 1.0) -> None:
    """Background thread polling state.json with X-Auth header."""
    while True:
        try:
            url = get_url_fn()
            pw = get_pw_fn()
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


# =========================
# PWM control functions
# =========================

PWM = build_pwm_backend()

# Global "current values" for display and for future integration (deadband etc.)
PWM_STATE = {
    "freq_hz": 200,   # default; adjust later
    "ch0": 0.0,       # duty cycle 0..100
    "ch1": 0.0,
}
PWM_MUTEX = threading.Lock()

def set_pwm_from_ui(session_id: str, ch0: float, ch1: float, freq_hz: int) -> Tuple[str, str]:
    """
    Set PWM channels. Enforces control lock:
    - If user doesn't hold control, values won't be applied.
    """
    with PWM_MUTEX:
        PWM_STATE["freq_hz"] = int(freq_hz)
        PWM_STATE["ch0"] = float(ch0)
        PWM_STATE["ch1"] = float(ch1)

    if not can_control(session_id):
        return ("⛔ Du hast kein Control. Werte werden nicht an PWM ausgegeben.",
                f"PWM0={ch0:.1f}%, PWM1={ch1:.1f}%, f={freq_hz}Hz (NICHT angewendet)")

    # Apply to backend
    PWM.set_pwm(channel=0, duty_cycle=float(ch0), frequency_hz=int(freq_hz))
    PWM.set_pwm(channel=1, duty_cycle=float(ch1), frequency_hz=int(freq_hz))
    return ("✅ PWM gesetzt.",
            f"PWM0={ch0:.1f}%, PWM1={ch1:.1f}%, f={freq_hz}Hz")


def pwm_stop(session_id: str) -> str:
    if not can_control(session_id):
        return "⛔ Du hast kein Control."
    PWM.set_pwm(0, 0.0, int(PWM_STATE["freq_hz"]))
    PWM.set_pwm(1, 0.0, int(PWM_STATE["freq_hz"]))
    with PWM_MUTEX:
        PWM_STATE["ch0"] = 0.0
        PWM_STATE["ch1"] = 0.0
    return "🛑 PWM auf 0% gesetzt."


# =========================
# System actions
# =========================

def system_action(action: str) -> str:
    """
    Executes reboot/shutdown only if ENABLE_SYSTEM_ACTIONS=1.
    On Raspberry Pi, you typically need sudo rights for these commands.
    """
    if os.getenv("ENABLE_SYSTEM_ACTIONS", "0") != "1":
        return "⚠️ System-Aktionen sind deaktiviert. Setze ENABLE_SYSTEM_ACTIONS=1 um zu aktivieren."

    cmd = None
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
# Gradio UI
# =========================

def make_session_id() -> str:
    return str(uuid.uuid4())

def build_webrtc_iframe(url: str) -> str:
    """
    Embed a web page (e.g., MediaMTX WebRTC player UI) via iframe.
    If your WebRTC UI is on a different path, adjust the URL in the textbox.
    """
    safe_url = (url or "").strip()
    if not safe_url:
        return "<div style='padding:12px'>Kein Stream-URL gesetzt.</div>"
    return f"""
    <iframe
        src="{safe_url}"
        style="width:100%; height:520px; border:0; border-radius:12px;"
        allow="camera; microphone; autoplay; encrypted-media; fullscreen; picture-in-picture"
    ></iframe>
    """

def app() -> gr.Blocks:
    with gr.Blocks(title="RudderPi Control") as demo:
        # Per-session id for lock logic
        session_id = gr.State(make_session_id)

        gr.Markdown("## RudderPi – Control UI (WSL-Test / Pi-Ready Skeleton)")

        with gr.Row():
            controller_name = gr.Textbox(label="Controller-Name", value="Browser", max_lines=1)
            lock_status = gr.Markdown("🔒 Control belegt von: niemand")
            with gr.Column(scale=0):
                btn_take = gr.Button("Control übernehmen", variant="primary")
                btn_release = gr.Button("Control freigeben")

        # Timer that triggers periodic updates (heartbeat, state refresh, etc.)
        hb_timer = gr.Timer(3.0)  # seconds

        # Heartbeat every few seconds so lock doesn't expire while controlling
        # Uses gradio "every" refresh on a hidden output.
        hb_out = gr.Textbox(visible=False)
        
        def on_take(sid: str, name: str) -> str:
            ok, msg = take_control(sid, name)
            return msg

        def on_release(sid: str) -> str:
            return release_control(sid)

        def on_hb(sid: str) -> Tuple[str, str]:
            status = heartbeat(sid)
            # update both visible and hidden outputs
            return status, status

        btn_take.click(on_take, inputs=[session_id, controller_name], outputs=[lock_status])
        btn_release.click(on_release, inputs=[session_id], outputs=[lock_status])

        # Auto heartbeat/refresh
        demo.load(on_hb, inputs=[session_id], outputs=[lock_status, hb_out])
        hb_timer.tick(on_hb, inputs=[session_id], outputs=[lock_status, hb_out])

        with gr.Tabs():
            with gr.Tab("Manual"):
                with gr.Row():
                    stream_url = gr.Textbox(
                        label="MediaMTX WebRTC URL (iframe)",
                        value="http://localhost:8889/",
                        max_lines=1,
                    )
                stream_view = gr.HTML(build_webrtc_iframe("http://localhost:8889/"))
                stream_url.change(lambda u: build_webrtc_iframe(u), inputs=[stream_url], outputs=[stream_view])

                gr.Markdown("### state.json (Polling)")
                with gr.Row():
                    state_url = gr.Textbox(
                        label="State URL",
                        value="http://192.168.42.129/state.json",
                        max_lines=1,
                    )
                    xauth_pw = gr.Textbox(
                        label="X-Auth Password",
                        value="rudderpi",
                        type="password",
                        max_lines=1,
                    )
                state_text = gr.Code(label="state.json (live)", language="json")

                # Start background poller once (when app loads)
                # We pass lambdas capturing the latest textbox values by using closures updated via gr.State holders.
                state_url_state = gr.State("http://192.168.42.129/state.json")
                xauth_pw_state = gr.State("rudderpi")

                state_url.change(lambda v: v.strip(), inputs=[state_url], outputs=[state_url_state])
                xauth_pw.change(lambda v: v, inputs=[xauth_pw], outputs=[xauth_pw_state])

                def start_poller(url: str, pw: str) -> str:
                    # Update states first
                    nonlocal poller_started
                    if not poller_started:
                        def get_url():
                            return state_url_state.value  # type: ignore[attr-defined]
                        def get_pw():
                            return xauth_pw_state.value    # type: ignore[attr-defined]
                        t = threading.Thread(target=poll_state_forever, args=(get_url, get_pw, 1.0), daemon=True)
                        t.start()
                        poller_started = True
                    return get_state_pretty()

                gr.Markdown("### PWM (Hardware PWM0 / PWM1 später am Pi)")
                with gr.Row():
                    pwm_freq = gr.Slider(50, 2000, value=200, step=10, label="PWM Frequency (Hz)")
                with gr.Row():
                    pwm0 = gr.Slider(0, 100, value=0, step=0.5, label="PWM0 Duty (%)")
                    pwm1 = gr.Slider(0, 100, value=0, step=0.5, label="PWM1 Duty (%)")
                with gr.Row():
                    btn_stop = gr.Button("🛑 Stop (0%)", variant="stop")
                    pwm_status = gr.Markdown("")

                # Apply PWM when sliders move (rate limited by gradio events)
                pwm0.change(set_pwm_from_ui, inputs=[session_id, pwm0, pwm1, pwm_freq], outputs=[pwm_status, hb_out])
                pwm1.change(set_pwm_from_ui, inputs=[session_id, pwm0, pwm1, pwm_freq], outputs=[pwm_status, hb_out])
                pwm_freq.change(set_pwm_from_ui, inputs=[session_id, pwm0, pwm1, pwm_freq], outputs=[pwm_status, hb_out])
                btn_stop.click(pwm_stop, inputs=[session_id], outputs=[pwm_status])

                # Live refresh of state.json view
                poller_started = False
                demo.load(start_poller, inputs=[state_url_state, xauth_pw_state], outputs=[state_text])
                state_timer = gr.Timer(1.0)
                demo.load(lambda: get_state_pretty(), outputs=[state_text])
                state_timer.tick(lambda: get_state_pretty(), outputs=[state_text])
                
                
            with gr.Tab("Auto"):
                gr.Markdown("Hier kommt später der Autopilot rein: Waypoints, Heading-Hold, Modus-States, Logs, etc.")
                gr.Markdown("- Vorschlag für Start: **Heading-Hold PID** + **Fake-GPS/Sim** Umschalter + **Safety-Kill**")

            with gr.Tab("System"):
                gr.Markdown("### System Actions (standardmäßig deaktiviert)")
                gr.Markdown("Aktiviere mit `ENABLE_SYSTEM_ACTIONS=1` (und sudo-Rechte am Pi).")
                with gr.Row():
                    btn_reboot = gr.Button("🔄 Reboot")
                    btn_shutdown = gr.Button("⏻ Shutdown", variant="stop")
                sys_out = gr.Markdown()

                btn_reboot.click(lambda: system_action("reboot"), outputs=[sys_out])
                btn_shutdown.click(lambda: system_action("shutdown"), outputs=[sys_out])

                gr.Markdown("### Sinnvoll zum Start (Vorschläge)")
                gr.Markdown(
                    "- **Health Panel**: CPU Temp, Throttle, RAM, Disk, Uptime\n"
                    "- **Network**: aktuelle IPs, VPN Status\n"
                    "- **Logging**: letzter N Zeilen Log + Download\n"
                    "- **Safety**: PWM watchdog (wenn kein Heartbeat -> PWM 0)\n"
                )

    return demo


if __name__ == "__main__":
    # Gradio server settings
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "7860"))
    app().launch(
        server_name=host,
        server_port=port,
        theme=gr.themes.Soft(),
    )
