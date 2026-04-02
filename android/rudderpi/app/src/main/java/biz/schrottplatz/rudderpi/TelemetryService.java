package biz.schrottplatz.rudderpi;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.IBinder;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;
import android.util.Log;

import com.pedro.rtplibrary.rtmp.RtmpCamera1;
import com.pedro.rtplibrary.rtsp.RtspCamera1;
import com.pedro.rtplibrary.rtsp.RtspCamera2;
import com.pedro.rtsp.rtsp.RtspClient;
import com.pedro.rtsp.utils.ConnectCheckerRtsp;

import fi.iki.elonen.NanoHTTPD;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.IntentFilter;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.os.BatteryManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

import java.io.IOException;


public class TelemetryService extends Service implements SensorEventListener {

    public static final String ACTION_START = "biz.schrottplatz.rudderpi.action.START";
    public static final String ACTION_STOP  = "biz.schrottplatz.rudderpi.action.STOP";

    private static final int NOTIF_ID = 1001;
    private static final String NOTIF_CHANNEL_ID = "rudderpi_telemetry";

    private HttpServer server;

    private SensorManager sensorManager;
    private LocationManager locationManager;

    private RtspCamera2 rtspCamera;

    private boolean torchEnabled = false;



    // Für Heading-Berechnung:
    private float[] lastAccel = null;
    private float[] lastMag = null;

    // --- Device Info ---
    private BroadcastReceiver batteryReceiver;
    private Handler deviceHandler;
    private Runnable devicePoller;

    private int lastBatteryPct = -1;
    private boolean lastCharging = false;
    private float lastTempC = Float.NaN;

    @Override
    public void onCreate() {
        super.onCreate();

        createNotificationChannel();
        startForeground(1, buildNotification("rudderpi telemetry running"));

        // Optional: jetzt erst initialisieren
    }


    public void requestStartVideo() {
        // optional: check camera permission bevor du startest
        Intent svc = new Intent(this, VideoService.class);
        ContextCompat.startForegroundService(this, svc);
    }

    public void requestStopVideo() {
        Intent svc = new Intent(this, VideoService.class);
        stopService(svc);
    }

    public boolean isVideoRunning() {
        // schnell & simpel: VideoService setzt ein static Flag
        return VideoService.isRunning();
    }


    private final LocationListener locationListener = new LocationListener() {
        @Override
        public void onLocationChanged(Location loc) {
            //Log.d("THREAD", "onLocationChanged on " + Thread.currentThread().getName());
            double lat = loc.getLatitude();
            double lon = loc.getLongitude();
            float accM = loc.hasAccuracy() ? loc.getAccuracy() : Float.NaN;
            double altM = loc.hasAltitude() ? loc.getAltitude() : Double.NaN;
            float speed = loc.hasSpeed() ? loc.getSpeed() : Float.NaN;

            TelemetryState.get().updateLocation(lat, lon, accM, altM, speed);
        }
    };

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        //Log.d("THREAD", "onStartCommand on " + Thread.currentThread().getName());
        String action = (intent != null) ? intent.getAction() : null;

        if (ACTION_STOP.equals(action)) {
            stopSelf();
            return START_NOT_STICKY;
        }

        // Android 14: Foreground + Notification sofort
        createNotificationChannel();
        startForeground(NOTIF_ID, buildNotification("Running"));

        startHttpServer();
        startSensors();
        startLocation();
        startDeviceInfo();

        return START_STICKY;
    }

    private void startHttpServer() {
        if (server != null) return;

        server = new HttpServer(8080, this);
        try {
            server.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false);
        } catch (IOException e) {
            e.printStackTrace();
        }
    }


    private void startSensors() {
        if (sensorManager != null) return;

        sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);

        Sensor accel = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
        Sensor mag = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD);

        if (accel != null) {
            sensorManager.registerListener(this, accel, SensorManager.SENSOR_DELAY_GAME);
        }
        if (mag != null) {
            sensorManager.registerListener(this, mag, SensorManager.SENSOR_DELAY_GAME);
        }
    }

    private void startLocation() {
        if (locationManager != null) return;
        locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);

        // Permission check (Android 14)
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            // Kein Location-Recht: wir lassen Location einfach weg
            return;
        }

        try {
            // GNSS (GPS)
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER,
                        1000L,   // minTime ms
                        0.0f,    // minDistance m
                        locationListener
                );
            }

            // Optional: Network provider als fallback
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.NETWORK_PROVIDER,
                        2000L,
                        0.0f,
                        locationListener
                );
            }
        } catch (SecurityException ignored) {
        }
    }

    private void startDeviceInfo() {
        if (deviceHandler != null) return;

        deviceHandler = new Handler(Looper.getMainLooper());

        // 1) Battery / Charging / Temp (event-basiert)
        if (batteryReceiver == null) {
            batteryReceiver = new BroadcastReceiver() {
                @Override
                public void onReceive(Context context, Intent intent) {
                    if (intent == null) return;

                    int level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
                    int scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
                    if (level >= 0 && scale > 0) {
                        lastBatteryPct = (int) Math.round(level * 100.0 / scale);
                    }

                    int status = intent.getIntExtra(
                            BatteryManager.EXTRA_STATUS,
                            BatteryManager.BATTERY_STATUS_UNKNOWN
                    );
                    lastCharging = (status == BatteryManager.BATTERY_STATUS_CHARGING
                            || status == BatteryManager.BATTERY_STATUS_FULL);

                    int tempTenths = intent.getIntExtra(
                            BatteryManager.EXTRA_TEMPERATURE,
                            Integer.MIN_VALUE
                    );
                    if (tempTenths != Integer.MIN_VALUE) {
                        lastTempC = tempTenths / 10.0f;
                    }

                    // sofort pushen, damit device-Block schnell befüllt ist
                    publishDeviceState();
                }
            };

            IntentFilter f = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
            // registerReceiver liefert bei ACTION_BATTERY_CHANGED sofort den aktuellen Intent zurück
            Intent sticky = registerReceiver(batteryReceiver, f);
            if (sticky != null) {
                // einmalig auswerten (damit du nicht auf das nächste Battery-Event warten musst)
                batteryReceiver.onReceive(this, sticky);
            }
        }

        // 2) Network + Uptime periodisch (z.B. alle 5s)
        devicePoller = new Runnable() {
            @Override
            public void run() {
                publishDeviceState();
                if (deviceHandler != null) {
                    deviceHandler.postDelayed(this, 5000L);
                }
            }
        };
        deviceHandler.post(devicePoller);
    }

    private void stopDeviceInfo() {
        // Poller stoppen
        if (deviceHandler != null && devicePoller != null) {
            deviceHandler.removeCallbacks(devicePoller);
        }
        devicePoller = null;
        deviceHandler = null;

        // Receiver abmelden
        if (batteryReceiver != null) {
            try {
                unregisterReceiver(batteryReceiver);
            } catch (IllegalArgumentException ignored) {
                // falls bereits abgemeldet
            }
            batteryReceiver = null;
        }
    }

    private void publishDeviceState() {
        String net = getNetworkTypeSimple();
        long uptimeS = SystemClock.elapsedRealtime() / 1000L;

        TelemetryState.get().updateDevice(
                lastBatteryPct,
                lastCharging,
                lastTempC,
                net,
                uptimeS
        );
    }

    private String getNetworkTypeSimple() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        if (cm == null) return "unknown";

        Network n = cm.getActiveNetwork();
        if (n == null) return "none";

        NetworkCapabilities caps = cm.getNetworkCapabilities(n);
        if (caps == null) return "unknown";

        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return "wifi";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return "ethernet";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return "cellular";
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) return "vpn";

        return "other";
    }

    @Override
    public void onDestroy() {
        super.onDestroy();

        // HTTP
        if (server != null) {
            server.stop();
            server = null;
        }

        // Sensor
        if (sensorManager != null) {
            sensorManager.unregisterListener(this);
            sensorManager = null;
        }

        // Location
        if (locationManager != null) {
            try {
                locationManager.removeUpdates(locationListener);
            } catch (SecurityException ignored) {}
            locationManager = null;
        }

        stopDeviceInfo();

        stopForeground(true);
    }

    // --- Sensor callbacks ---
    @Override
    public void onSensorChanged(SensorEvent event) {
        //Log.d("THREAD", "onSensorChanged on " + Thread.currentThread().getName());
        if (event.sensor.getType() == Sensor.TYPE_ACCELEROMETER) {
            lastAccel = event.values.clone();
        } else if (event.sensor.getType() == Sensor.TYPE_MAGNETIC_FIELD) {
            lastMag = event.values.clone();
        }

        // Wenn beide vorhanden: Heading berechnen
        if (lastAccel != null && lastMag != null) {
            float[] R = new float[9];
            float[] I = new float[9];
            boolean ok = SensorManager.getRotationMatrix(R, I, lastAccel, lastMag);
            if (ok) {
                float[] ori = new float[3];
                SensorManager.getOrientation(R, ori);
                // ori[0] = azimuth (rad) -> deg 0..360
                float azimuthDeg = (float) Math.toDegrees(ori[0]);
                if (azimuthDeg < 0) azimuthDeg += 360.0f;

                TelemetryState.get().updateImu(lastAccel, lastMag, azimuthDeg);
            }
        }
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) { }

    // --- Foreground notification ---
    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    NOTIF_CHANNEL_ID,
                    "RudderPi Telemetry",
                    NotificationManager.IMPORTANCE_LOW
            );
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    private Notification buildNotification(String text) {
        return new NotificationCompat.Builder(this, NOTIF_CHANNEL_ID)
                .setContentTitle("RudderPi Telemetry")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_upload) // später eigenes Icon
                .setOngoing(true)
                .build();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null; // kein Binding nötig
    }

    public synchronized boolean setTorchEnabled(boolean enabled) {
        VideoService vs = VideoService.getInstance();
        if (vs == null) {
            Log.w("TORCH", "VideoService not running");
            return false;
        }
        return vs.setTorchEnabled(enabled);
    }

    public synchronized boolean isTorchEnabled() {
        VideoService vs = VideoService.getInstance();
        return vs != null && vs.isTorchEnabled();
    }
}
