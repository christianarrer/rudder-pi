# Architecture (rudder-pi)

## Scope

rudder-pi is a modular Raspberry Pi based control system for a 3D printed (FDM) model boat.

Primary goals:
- Reliable and safe motor control (fail-safe by default)
- Local status/control UI (works without internet)
- Sensor integration via an Android phone (GPS, camera, IMU, LTE via USB tethering)
- Logging and basic watchdogs for unattended operation

Non-goals (at least initially):
- Fully autonomous navigation
- Safety-critical use on manned vessels
- Complex cloud backends (keep it local-first)

Parts of the codebase may be developed with AI assistance. All code is reviewed, understood, and maintained by the project owner.

## System Overview

### Raspberry Pi (Boat Computer)
Responsibilities:
- Motor control (left/right or throttle/steering depending on drivetrain)
- Safety logic (heartbeat, timeouts, emergency stop behavior)
- Local web UI for status + manual control
- Logging (events + sensor data)
- Optional remote access via VPN (later phase)

### Android Phone (Sensor & Modem Node)
Connection: USB tethering to the Raspberry Pi

Responsibilities:
- Provides LTE connectivity to the Raspberry Pi (as upstream internet)
- Provides GPS data to Raspberry Pi
- Provides camera data (start with JPEG snapshots; streaming later)
- Provides IMU data (accel/gyro) and phone health metrics (battery, temp, signal)

### Operator / Remote
- Local: connect to the Pi’s web UI over Wi-Fi/LAN
- Remote (later): connect via VPN (e.g., Tailscale/WireGuard) to the same UI

## Hardware Assumptions (current target)

- Battery: NiMH 7.4 V (note: confirm actual cell count/nominal voltage in hardware docs)
- Motor controller / ESC: ABSIMA ECU-1
- Drive: dual motors (typical differential thrust) OR motor + rudder (to be confirmed)
- Raspberry Pi: any model capable of stable networking + PWM/GPIO (exact model optional)
- EMI/Noise expected from brushed motors -> wiring and filtering required (see docs/hardware)

## Safety Principles (must-have)

Fail-safe behavior is a feature, not an afterthought.

Minimum rules:
1. Default output = STOP (no signal / no heartbeat -> stop)
2. Explicit arming required (software arm + optional hardware arm switch later)
3. Rate limiting / ramping on throttle changes (avoid sudden spikes)
4. E-stop path: immediate stop command has priority over everything
5. Brownout/USB dropouts must not cause runaway (driver defaults to safe state)

## Data Flows

### Phone -> Pi (Sensors)
Transport: simple local HTTP endpoints (first implementation), optionally MQTT later.

Endpoints (initial plan):
- `/gps` -> JSON (lat, lon, speed, course, fix quality, timestamp)
- `/imu` -> JSON (accel, gyro, optional orientation)
- `/snapshot.jpg` -> latest camera frame (JPEG)
- `/status` -> JSON (battery %, temp, charging, network type, uptime)

### Pi -> Phone (Status Display)
Preferred simple approach:
- Pi hosts a local web dashboard
- Phone opens it in fullscreen (kiosk-ish) to show Pi status

Optional later:
- Pi sends small status text updates to the phone (HTTP push)

### Pi -> Operator (Control/UI)
- Local web UI (single page)
- Shows: motor state, arming state, last heartbeat time, sensor health, network status, logs
- Manual control: throttle/steering sliders + E-stop

### Pi -> Storage/Telemetry
- Local log files first
- Optional later: InfluxDB + Grafana, or plain CSV rotation

## Software Components (planned)

On Raspberry Pi:
- `motor_control`: outputs safe PWM/GPIO to ESC/driver, implements arming + failsafe
- `status_server`: local web UI + JSON status endpoint
- `sensor_client`: pulls sensor data from phone endpoints and caches latest values
- `watchdog`: restarts components, detects hangs/network dropouts

On Phone (Termux-based):
- `sensor_server`: exposes GPS/IMU/status endpoints, plus camera snapshot service
- `boot_start`: ensures services run after reboot and recover after crashes

## Interfaces (contracts)

Motor control contract:
- Inputs: desired throttle/steering + arming state + heartbeat
- Output: ESC/driver signals
- Hard rule: if not armed OR heartbeat expired -> STOP output

Sensor contract:
- Each sensor endpoint must include a timestamp and a validity indicator
- Pi treats missing/stale data as "unhealthy" but continues operating locally unless safety requires stop

## Build/Deployment Philosophy

- Keep dependencies minimal, prefer Python on the Pi (fast iteration)
- Services should be systemd units on the Pi
- Phone runs Termux + boot scripts
- Everything is reproducible from docs + scripts
