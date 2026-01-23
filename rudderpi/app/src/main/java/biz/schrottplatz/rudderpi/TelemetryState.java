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

    // Location
    private double lat = Double.NaN, lon = Double.NaN;
    private float accM = Float.NaN, speedMps = Float.NaN;
    private double altM = Double.NaN;

    private long tsMs = 0;

    public synchronized void updateImu(float[] accel3, float[] mag3, float headingDeg) {
        this.accel = accel3.clone();
        this.mag = mag3.clone();
        this.headingDeg = headingDeg;
        this.tsMs = System.currentTimeMillis();
    }

    public synchronized void updateLocation(double lat, double lon, float accM, double altM, float speedMps) {
        this.lat = lat;
        this.lon = lon;
        this.accM = accM;
        this.altM = altM;
        this.speedMps = speedMps;
        this.tsMs = System.currentTimeMillis();
    }

    public synchronized String toJson() {
        JSONObject root = null;
        try {
            root = new JSONObject();
            JSONObject imu = new JSONObject();
            JSONObject loc = new JSONObject();

            imu.put("heading_deg", headingDeg);
            imu.put("accel_mps2", new JSONArray(accel));
            imu.put("mag_uT", new JSONArray(mag));

            loc.put("lat", lat);
            loc.put("lon", lon);
            loc.put("acc_m", accM);
            loc.put("alt_m", altM);
            loc.put("speed_mps", speedMps);

            root.put("ts_ms", tsMs);
            root.put("imu", imu);
            root.put("location", loc);
        } catch (Exception ignored) {
        }

        return root.toString();
    }
}
