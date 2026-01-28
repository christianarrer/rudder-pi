package biz.schrottplatz.rudderpi;

import org.json.JSONArray;
import org.json.JSONObject;

public class TelemetryState {
    private static final TelemetryState INSTANCE = new TelemetryState();

    public static TelemetryState get() {
        return INSTANCE;
    }

    private TelemetryState() {}

    // Sensor
    private float[] accel = new float[] {Float.NaN, Float.NaN, Float.NaN};
    private float[] mag   = new float[] {Float.NaN, Float.NaN, Float.NaN};
    private float headingDeg = Float.NaN;
    private long imuTsMs = 0;

    // Location
    private double lat = Double.NaN, lon = Double.NaN;
    private float accM = Float.NaN, speedMps = Float.NaN;
    private double altM = Double.NaN;
    private long locationTsMs = 0;

    // Device
    private int batteryPct = -1;
    private boolean charging = false;
    private float tempC = Float.NaN;
    private String network = "unknown";
    private long uptimeS = 0;
    private long deviceTsMs = 0;

    public synchronized void updateImu(float[] accel3, float[] mag3, float headingDeg) {
        this.accel = accel3.clone();
        this.mag = mag3.clone();
        this.headingDeg = headingDeg;
        this.imuTsMs = System.currentTimeMillis();
    }

    public synchronized void updateLocation(double lat, double lon, float accM, double altM, float speedMps) {
        this.lat = lat;
        this.lon = lon;
        this.accM = accM;
        this.altM = altM;
        this.speedMps = speedMps;
        this.locationTsMs = System.currentTimeMillis();
    }

    public synchronized void updateDevice(int batteryPct, boolean charging, float tempC, String network, long uptimeS) {
        this.batteryPct = batteryPct;
        this.charging = charging;
        this.tempC = tempC;
        this.network = (network != null) ? network : "unknown";
        this.uptimeS = uptimeS;
        this.deviceTsMs = System.currentTimeMillis();
    }

    public synchronized String toJson() {
        try {
            JSONObject root = new JSONObject();

            JSONObject imu = new JSONObject();
            imu.put("ts_ms", imuTsMs);
            imu.put("heading_deg", headingDeg);
            imu.put("accel_mps2", new JSONArray(accel));
            imu.put("mag_uT", new JSONArray(mag));

            JSONObject loc = new JSONObject();
            loc.put("ts_ms", locationTsMs);
            loc.put("lat", lat);
            loc.put("lon", lon);
            loc.put("acc_m", accM);
            loc.put("alt_m", altM);
            loc.put("speed_mps", speedMps);

            JSONObject dev = new JSONObject();
            dev.put("ts_ms", deviceTsMs);
            dev.put("battery_pct", batteryPct);
            dev.put("charging", charging);
            dev.put("temp_c", tempC);
            dev.put("network", network);
            dev.put("uptime_s", uptimeS);

            // Dokument-Zeitpunkt (wann dieses JSON erzeugt wurde)
            root.put("ts_ms", System.currentTimeMillis());
            root.put("imu", imu);
            root.put("location", loc);
            root.put("device", dev);

            return root.toString();
        } catch (Exception ignored) {
            return "{}";
        }
    }
}

