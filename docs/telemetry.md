# Telemetry Reference – rudderpi

This document describes the telemetry data produced by the **rudderpi Android app**.
It is intended as a stable reference for development, debugging, logging, and future protocol evolution.

---

## Overview

Telemetry is emitted as a single JSON object containing:

- A timestamp
- IMU (inertial sensor) data
- GNSS / location data

Example:

```json
{
  "ts_ms": 1769372196662,
  "imu": {
    "heading_deg": 117.9359,
    "accel_mps2": [-1.1307, -3.2669, 9.0752],
    "mag_uT": [-25.6125, -54.1125, 135.75]
  },
  "location": {
    "lat": 47.80375525,
    "lon": 13.01451748,
    "acc_m": 4.2391,
    "alt_m": 476.1306,
    "speed_mps": 0
  }
}
```

All physical units follow **SI conventions** where applicable.

---

## Timestamp

### `ts_ms`

- **Type:** integer
- **Unit:** milliseconds
- **Reference:** Unix Epoch (1970-01-01T00:00:00Z)
- **Source:** Android system clock (`System.currentTimeMillis()`)

**Description:**

Absolute timestamp of the telemetry snapshot. Used for:

- Sensor synchronization
- Logging and replay
- Time-series analysis
- Filtering (e.g. moving average, Kalman filters)

---

## IMU – Inertial Measurement Unit

The IMU block aggregates data from multiple internal sensors:

- Accelerometer
- Gyroscope
- Magnetometer

### `heading_deg`

- **Type:** float
- **Unit:** degrees (°)
- **Range:** 0–360
- **Source:** Sensor fusion (magnetometer + gyro + accelerometer)

**Description:**

Absolute heading relative to geographic north.

- `0°` = North
- `90°` = East
- `180°` = South
- `270°` = West

Used for vessel orientation, course display, and future autopilot logic.

---

### `accel_mps2`

- **Type:** array of 3 floats
- **Unit:** meters per second squared (m/s²)
- **Source:** Accelerometer

**Axis definition (Android standard):**

- **X:** left (+) / right (−)
- **Y:** forward (+) / backward (−)
- **Z:** upward (+)

**Description:**

Linear acceleration including gravity.

At rest, the Z axis typically measures approximately `+9.81 m/s²` due to Earth’s gravitational acceleration.

Used for:

- Motion detection
- Shock / vibration analysis
- Deriving pitch and roll (future)

---

### `mag_uT`

- **Type:** array of 3 floats
- **Unit:** microtesla (µT)
- **Source:** Magnetometer

**Description:**

Earth’s magnetic field vector along the three device axes.

Typical Earth magnetic field strength: ~25–65 µT (location dependent).

Used primarily for:

- Compass heading
- Gyroscope drift correction

**Note:**

Magnetometer readings are sensitive to:

- Electric motors
- Current-carrying wires
- Ferromagnetic materials

Calibration and careful placement are recommended.

---

## Location – GNSS / Android Location Provider

### `lat`

- **Type:** float
- **Unit:** degrees (WGS84)
- **Description:** Latitude

---

### `lon`

- **Type:** float
- **Unit:** degrees (WGS84)
- **Description:** Longitude

---

### `acc_m`

- **Type:** float
- **Unit:** meters
- **Source:** GNSS accuracy estimate

**Description:**

Estimated horizontal position accuracy (1-sigma radius).

Example:

- `acc_m = 4.2` → true position likely within ~4 m radius

---

### `alt_m`

- **Type:** float
- **Unit:** meters above mean sea level
- **Source:** GNSS

**Description:**

Altitude derived from satellite navigation.

Note: GNSS altitude is typically less accurate than horizontal position.

---

### `speed_mps`

- **Type:** float
- **Unit:** meters per second (m/s)
- **Source:** GNSS Doppler measurement

**Description:**

Speed over ground.

Conversion:

- `km/h = m/s × 3.6`

Used for:

- Velocity display
- Motion state detection
- Control and regulation logic

---

## Notes & Future Extensions

Planned or possible future additions:

- Gyroscope angular velocity (`gyro_radps`)
- Pitch / roll angles
- Filtered vs. raw sensor separation
- Battery and system health metrics
- Quality flags (GNSS fix type, sensor validity)

---

## Versioning

This document describes telemetry format as of **rudderpi 0.1-beta**.

Breaking changes should be documented here and reflected in the JSON schema.

