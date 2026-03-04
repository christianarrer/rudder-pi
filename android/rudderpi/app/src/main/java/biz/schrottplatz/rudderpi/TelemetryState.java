package biz.schrottplatz.rudderpi;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

public class TelemetryState {
    private static final TelemetryState INSTANCE = new TelemetryState();

    public static TelemetryState get() {
        return INSTANCE;
    }

    private TelemetryState() {}

    // IMU
    private float[] accel = new float[] {Float.NaN, Float.NaN, Float.NaN};
    private float[] mag   = new float[] {Float.NaN, Float.NaN, Float.NaN};
    private float headingDeg = Float.NaN;
    private long imuTsMs = 0;

    // Location (may be unavailable when GPS has no fix)
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
        this.accel = (accel3 != null) ? accel3.clone() : new float[] {Float.NaN, Float.NaN, Float.NaN};
        this.mag = (mag3 != null) ? mag3.clone() : new float[] {Float.NaN, Float.NaN, Float.NaN};
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

    private static void putFinite(JSONObject obj, String key, double value) throws Exception {
        if (Double.isFinite(value)) obj.put(key, value);
        else obj.put(key, JSONObject.NULL);
    }

    private static void putFinite(JSONObject obj, String key, float value) throws Exception {
        if (Float.isFinite(value)) obj.put(key, value);
        else obj.put(key, JSONObject.NULL);
    }

    private static JSONArray floatArrayOrNulls(float[] arr) throws JSONException {
        JSONArray a = new JSONArray();
        if (arr == null) {
            a.put(JSONObject.NULL);
            a.put(JSONObject.NULL);
            a.put(JSONObject.NULL);
            return a;
        }
        for (float v : arr) {
            if (Float.isFinite(v)) a.put(v);
            else a.put(JSONObject.NULL);
        }
        return a;
    }

    public synchronized String toJson() {
        try {
            JSONObject root = new JSONObject();

            JSONObject imu = new JSONObject();
            imu.put("ts_ms", imuTsMs);
            putFinite(imu, "heading_deg", headingDeg);
            imu.put("accel_mps2", floatArrayOrNulls(accel));
            imu.put("mag_uT", floatArrayOrNulls(mag));

            JSONObject loc = new JSONObject();
            loc.put("ts_ms", locationTsMs);
            putFinite(loc, "lat", lat);
            putFinite(loc, "lon", lon);
            putFinite(loc, "acc_m", accM);
            putFinite(loc, "alt_m", altM);
            putFinite(loc, "speed_mps", speedMps);

            // Optional: explicit validity flag (useful for logging/ML)
            loc.put("valid", Double.isFinite(lat) && Double.isFinite(lon));

            JSONObject dev = new JSONObject();
            dev.put("ts_ms", deviceTsMs);
            dev.put("battery_pct", batteryPct >= 0 ? batteryPct : JSONObject.NULL);
            dev.put("charging", charging);
            putFinite(dev, "temp_c", tempC);
            dev.put("network", network);
            dev.put("uptime_s", uptimeS);

            // Document timestamp (when this JSON was created)
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
