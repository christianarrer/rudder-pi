# Roadmap (rudder-pi)

This roadmap is organized into small, finishable milestones.
Each milestone should be "done" only when it survives reboot tests and has clear status reporting.

## Milestone 0 — Repo + Documentation Foundation
Goal: project structure exists and decisions are written down.
- [x] README with scope
- [ ] Architecture doc (this)
- [ ] Hardware notes (battery, ESC, wiring, EMI)
- [ ] Basic issue labels / milestones (optional)

Definition of done:
- New device can clone repo and understand the system at a high level in <10 minutes.

## Milestone 1 — Connectivity Baseline (USB Tether)
Goal: Pi has reliable upstream internet via phone USB tethering.
- [ ] Document USB tether setup (phone + Pi)
- [ ] Pi can reach internet and resolves DNS
- [ ] Simple healthcheck script/log (connectivity up/down)

Definition of done:
- Power cycle phone and Pi -> tether comes back and status is visible.

## Milestone 2 — Local Status UI (Pi)
Goal: one place to see if the boat computer is healthy.
- [ ] Local web dashboard (minimal)
- [ ] Shows CPU temp, uptime, storage, network state
- [ ] Shows "armed/disarmed" placeholder and "last sensor update" placeholders

Definition of done:
- UI loads fast, works offline, and clearly indicates failures.

## Milestone 3 — GPS from Phone
Goal: Pi can read and display GPS.
- [ ] Phone exposes `/gps` JSON
- [ ] Pi pulls GPS, validates timestamp, caches latest fix
- [ ] UI shows fix/no-fix, speed, course, timestamp

Definition of done:
- Reboot tests OK, stale GPS is detected and shown as stale.

## Milestone 4 — Camera Snapshot from Phone
Goal: forward-facing situational awareness.
- [ ] Phone exposes `/snapshot.jpg`
- [ ] Pi displays snapshot in UI (and/or stores snapshots)
- [ ] Rate limit to avoid overheating/data flood

Definition of done:
- Snapshot works continuously for a long test run without storage explosion.

## Milestone 5 — Motor Control: Safe Bring-up
Goal: motor outputs are controllable and fail safe.
- [ ] Decide/control mode (differential thrust vs rudder+throttle)
- [ ] Implement arming + STOP default
- [ ] Implement heartbeat timeout -> STOP
- [ ] Implement throttle ramp limiting
- [ ] UI manual control + E-stop

Definition of done:
- Kill UI session / disconnect network -> motors stop within timeout.
- Reboot -> starts in DISARMED, motors never spin by default.

## Milestone 6 — Phone IMU (Accel/Gyro)
Goal: motion data for fun + diagnostics.
- [ ] Phone exposes `/imu` JSON
- [ ] Pi logs + shows simple values
- [ ] Optional: roll/pitch estimate (only if stable)

Definition of done:
- Data is stable enough to be meaningful and does not impact performance.

## Milestone 7 — Remote Access (VPN)
Goal: reach the boat computer over LTE safely.
- [ ] Choose VPN (Tailscale or WireGuard)
- [ ] Remote access to UI
- [ ] Strict authentication (VPN first, then UI)

Definition of done:
- Remote UI access works without exposing services publicly.

## Milestone 8 — Watchdogs & Hardening
Goal: unattended reliability.
- [ ] systemd services for all components
- [ ] restart-on-failure policies
- [ ] log rotation
- [ ] clear alarm states in UI

Definition of done:
- Long soak test: survives intermittent LTE, phone reconnects, no runaway logs.

## Later / Optional
- Geofence / return-to-home experiments
- Higher-quality video streaming (MJPEG -> RTSP/WebRTC)
- Power monitoring (current/voltage) and thermal management
