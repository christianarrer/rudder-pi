package biz.schrottplatz.rudderpi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import com.pedro.rtplibrary.rtsp.RtspCamera2;
import com.pedro.rtsp.utils.ConnectCheckerRtsp;

import biz.schrottplatz.rudderpi.util.NetUtil;

public class VideoService extends Service {

    public static final String PREFS_NAME = "video_service_prefs";
    public static final String PREF_LAST_STATUS = "last_status";

    public static final String ACTION_START = "biz.schrottplatz.rudderpi.action.VIDEO_START";
    public static final String ACTION_STOP  = "biz.schrottplatz.rudderpi.action.VIDEO_STOP";

    public static final String EXTRA_RTSP_URL = "extra_rtsp_url";

    public static final String ACTION_STATUS = "biz.schrottplatz.rudderpi.action.VIDEO_STATUS";
    public static final String EXTRA_STATUS_TEXT = "extra_status_text";

    private static final int NOTIF_ID = 1001;
    private static final String CHANNEL_ID = "video_service";

    private static final String TAG = "VideoService";

    // ============================================================
    // 1) Zwei getrennte Flags (wichtig!)
    //    - serviceRunning: Lebenszyklus des Services / Reconnect-Threads
    //    - streamRunning:  Zustand des RTSP-Streams
    // ============================================================
    private volatile boolean serviceRunning = false;
    private volatile boolean streamRunning  = false;

    // ============================================================
    // 2) stateLock: Treffpunkt für wait()/notifyAll()
    //    - Reconnect-Thread schläft auf stateLock.wait()
    //    - Callbacks wecken ihn mit stateLock.notifyAll()
    // ============================================================
    private final Object stateLock = new Object();

    // ============================================================
    // 3) Reconnect-Thread + Backoff
    // ============================================================
    private Thread rtspThread;
    private int backoffMs = 1000;
    private static final int BACKOFF_MAX_MS = 30_000;

    // ============================================================
    // 4) RTSP Settings (aus SharedPreferences geladen)
    //    volatile: weil UI/Prefs und Thread parallel zugreifen können.
    // ============================================================
    private volatile String rtspRemoteServerIP4 = "";
    private volatile int rtspRemoteServerPort = 8554;

    // Fixer Pfad (wie du wolltest)
    private static final String RTSP_PATH = "/rudderpi";

    // ============================================================
    // 5) RtspCamera2 Instanz
    // ============================================================
    private RtspCamera2 rtspCamera;

    // ============================================================
    // 6) Ein Flag, das sagt: "Bitte reconnecten"
    //    - wird bei Disconnect/Fail gesetzt
    //    - wird auch beim Start gesetzt
    // ============================================================
    private volatile boolean wantReconnect = true;

    private static volatile boolean RUNNING = false;
    public static boolean isRunning() { return RUNNING; }


    // ============================================================
    // 7) RTSP Callback: informiert & weckt Loop auf
    // ============================================================
    private final ConnectCheckerRtsp connectChecker = new ConnectCheckerRtsp() {

        @Override
        public void onConnectionStartedRtsp(String rtspUrl) {
            Log.i(TAG, "RTSP: connection started: " + rtspUrl);
            postStatus("RTSP: connecting...");
            // Hier noch kein notify nötig, weil noch nichts "entschieden" wurde.
        }

        @Override
        public void onConnectionSuccessRtsp() {
            Log.i(TAG, "RTSP: connection success");
            postStatus("RTSP: connected");

            // streamRunning = true wird in startStreaming gesetzt,
            // aber der Callback zeigt uns: die Verbindung ist wirklich da.
            // Wir wecken den Reconnect-Thread (falls er auf Erfolg wartet).
            synchronized (stateLock) {
                stateLock.notifyAll();
            }
        }

        @Override
        public void onConnectionFailedRtsp(String reason) {
            Log.e(TAG, "RTSP: connection failed: " + reason);
            postStatus("RTSP failed: " + reason);

            // Stream gilt als nicht laufend -> Reconnect erwünscht
            streamRunning = false;
            wantReconnect = true;

            // Reconnect-Thread soll sofort reagieren:
            synchronized (stateLock) {
                stateLock.notifyAll();
            }
        }

        @Override
        public void onNewBitrateRtsp(long bitrate) {
            Log.i(TAG, "RTSP: new bitrate: " + bitrate);
        }

        @Override
        public void onDisconnectRtsp() {
            Log.i(TAG, "RTSP: disconnected");
            postStatus("RTSP: disconnected");

            streamRunning = false;
            wantReconnect = true;

            synchronized (stateLock) {
                stateLock.notifyAll();
            }
        }

        @Override
        public void onAuthErrorRtsp() {
            Log.e(TAG, "RTSP: auth error");
            postStatus("RTSP: auth error");

            streamRunning = false;
            wantReconnect = true;

            synchronized (stateLock) {
                stateLock.notifyAll();
            }
        }

        @Override
        public void onAuthSuccessRtsp() {
            Log.i(TAG, "RTSP: auth success");
            postStatus("RTSP: auth ok");
        }
    };

    // ============================================================
    // Android Service Lifecycle
    // ============================================================

    @Override
    public void onCreate() {
        super.onCreate();

        RUNNING = true;

        createNotifChannel();
        startForeground(NOTIF_ID, buildNotification("Starting..."));

        postStatus("VideoService: onCreate()");

        // 1) Settings laden (IP/Port aus Prefs)
        loadRtspSettingsFromPrefs();

        // 2) RTSP Kamera initialisieren (einmal)
        //    Wichtig: hier NICHT streamen, nur vorbereiten / Objekt erstellen.
        initRtspCameraIfNeeded();

        // 3) Reconnect-Loop starten
        startReconnectThreadIfNeeded();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Optional: bei jedem Start nochmal Prefs laden.
        loadRtspSettingsFromPrefs();

        // Falls Thread aus irgendeinem Grund nicht läuft, neu starten.
        startReconnectThreadIfNeeded();

        // START_STICKY: Android darf Service nach Kill neu starten
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        postStatus("VideoService: onDestroy()");
        stopServiceAndThread();
        super.onDestroy();
        RUNNING = false;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ============================================================
    // Initialisierung
    // ============================================================

    private void initRtspCameraIfNeeded() {
        if (rtspCamera != null) return;

        // useOpengl false (wie bei dir)
        // ConnectChecker ist unser connectChecker oben
        rtspCamera = new RtspCamera2(getApplicationContext(), false, connectChecker);

        // Video-Encoder vorbereiten (einmal)
        boolean okVideo = rtspCamera.prepareVideo(
                1280,
                720,
                30,
                2000 * 1024,
                0
        );

        if (!okVideo) {
            Log.e(TAG, "RTSP: prepareVideo failed (encoder config not supported?)");
            postStatus("RTSP: encoder init failed");
        } else {
            Log.i(TAG, "RTSP: prepareVideo ok");
        }
    }

    // ============================================================
    // Reconnect Thread
    // ============================================================

    private void startReconnectThreadIfNeeded() {
        if (rtspThread != null && rtspThread.isAlive()) return;

        serviceRunning = true;
        wantReconnect = true; // beim Start gleich verbinden

        rtspThread = new Thread(this::rtspLoop, "rtsp-reconnect");
        rtspThread.start();
    }

    private void stopServiceAndThread() {
        serviceRunning = false;
        wantReconnect = false;

        // Thread wecken, falls er gerade wartet
        synchronized (stateLock) {
            stateLock.notifyAll();
        }

        // Stream stoppen
        stopStreaming();
    }

    private void rtspLoop() {
        backoffMs = 1000;

        while (serviceRunning) {

            // 1) Settings nachladen (damit Apply später wirkt)
            loadRtspSettingsFromPrefs();

            // 2) Prüfen, ob Settings gültig sind
            if (!NetUtil.isValidIPv4(rtspRemoteServerIP4) || !NetUtil.isValidTcpPort(rtspRemoteServerPort)) {
                postStatus("RTSP: waiting for valid settings...");
                sleepQuiet(1000);
                continue;
            }

            // 3) Wenn kein Reconnect nötig ist, schlafen wir ohne CPU-Last
            if (!wantReconnect) {
                waitOnStateLock(30_000);
                continue;
            }

            // 4) Wir versuchen jetzt zu verbinden
            wantReconnect = false;

            String rtspUrl = "rtsp://" + rtspRemoteServerIP4 + ":" + rtspRemoteServerPort + RTSP_PATH;
            postStatus("RTSP: start attempt: " + rtspUrl);

            // 5) Starten (synchronized)
            boolean started = startStreaming(rtspUrl);

            if (!started) {
                // startStreaming konnte nicht starten (z.B. prepareVideo fehlte)
                postStatus("RTSP: start failed. retry in " + backoffMs + "ms");
                sleepQuiet(backoffMs);
                backoffMs = Math.min(backoffMs * 2, BACKOFF_MAX_MS);
                wantReconnect = true;
                continue;
            }

            // 6) Wenn gestartet, warten wir bis:
            //    - disconnect / fail -> Callback setzt wantReconnect=true und notifyAll()
            //    - oder Service stoppt
            //
            // Wir schlafen wieder ohne CPU-Last:
            waitOnStateLock(60_000);

            // 7) Wenn Callback uns "Reconnect" signalisiert, stoppen wir sauber.
            if (wantReconnect && serviceRunning) {
                stopStreaming();
                sleepQuiet(500);
            }

            // Bei Erfolg resetten wir Backoff, damit wir nach einem echten Erfolg
            // nicht ewig lange warten müssen.
            if (!wantReconnect) {
                backoffMs = 1000;
            }
        }

        // Service stop -> sicherheitshalber stream stoppen
        stopStreaming();
        postStatus("RTSP: loop ended");
    }

    private void waitOnStateLock(long timeoutMs) {
        synchronized (stateLock) {
            try {
                stateLock.wait(timeoutMs);
            } catch (InterruptedException ignored) {
            }
        }
    }

    private void sleepQuiet(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }

    // ============================================================
    // Start/Stop Streaming (deine Methoden, minimal angepasst)
    // ============================================================

    /**
     * Startet das Streaming, wenn es nicht läuft.
     * @return true wenn der Start-Versuch grundsätzlich losgegangen ist, false wenn es unmöglich ist
     */
    private synchronized boolean startStreaming(String rtspUrl) {
        // streamRunning = Zustand "Stream läuft"
        if (streamRunning) {
            postStatus("VideoService: stream already running");
            return true;
        }

        postStatus("VideoService: starting stream to " + rtspUrl);

        // Kamera muss existieren und prepareVideo muss ok gewesen sein.
        if (rtspCamera == null) {
            postStatus("VideoService: rtspCamera is null -> init failed?");
            return false;
        }

        // Wenn Encoder-Setup bei init fehlte, ist startStream sinnlos:
        // (optional: du könntest hier nochmal prepareVideo versuchen)
        // Für minimalen Umbau: wir versuchen es, aber wenn prepareVideo false war,
        // wird es vermutlich nicht funktionieren.
        try {
            if (!rtspCamera.isOnPreview()) {
                rtspCamera.startPreview();
            }

            // WICHTIG: nicht hardcoden!
            rtspCamera.startStream(rtspUrl);

            streamRunning = true;
            updateNotification("Streaming: ON");

            return true;
        } catch (Exception e) {
            Log.e(TAG, "startStreaming exception", e);
            streamRunning = false;

            // WICHTIG: nach IllegalState am besten "hart" resetten
            safeResetRtspCamera();

            return false;
        }
    }

    private synchronized void safeResetRtspCamera() {
        try {
            if (rtspCamera != null) {
                if (rtspCamera.isStreaming()) rtspCamera.stopStream();
                if (rtspCamera.isOnPreview()) rtspCamera.stopPreview();
            }
        } catch (Exception ignored) {}

        rtspCamera = null;

        // neu initialisieren
        initRtspCameraIfNeeded();
    }

    private synchronized void stopStreaming() {
        if (!streamRunning) return;

        postStatus("VideoService: stopping stream...");

        try {
            if (rtspCamera != null) {
                if (rtspCamera.isStreaming()) rtspCamera.stopStream();
                if (rtspCamera.isOnPreview()) rtspCamera.stopPreview();
            }
        } catch (Exception e) {
            Log.w(TAG, "stopStreaming exception", e);
        } finally {
            streamRunning = false;
            updateNotification("Streaming: OFF");
            synchronized (stateLock) { stateLock.notifyAll(); }
        }

        // Reconnect-Thread soll ggf. sofort weiterlaufen
        synchronized (stateLock) {
            stateLock.notifyAll();
        }

        postStatus("VideoService: stopped");
    }

    // ============================================================
    // Settings laden (du hast das schon, hier nur der Rahmen)
    // ============================================================

    private void loadRtspSettingsFromPrefs() {
        // Beispiel:
        String ip = getSharedPreferences("app", MODE_PRIVATE)
                .getString("rtsp_remote_server_ipv4", "192.168.0.1");
        if (NetUtil.isValidIPv4(ip)) rtspRemoteServerIP4 = ip;

        int port = getSharedPreferences("app", MODE_PRIVATE)
                 .getInt("rtsp_remote_server_port", 8554);
        if (NetUtil.isValidTcpPort(port)) rtspRemoteServerPort = port;
    }

    // ============================================================
    // Deine Status/Notification Helpers
    // ============================================================

    private void postStatus(String msg) {
        Log.i(TAG, msg);

        // 1) Persistenter Status
        SharedPreferences prefs =
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit()
                .putString(PREF_LAST_STATUS, msg)
                .apply();

        // 2) Optional: Live-Update für UI
        Intent i = new Intent(ACTION_STATUS);
        i.setPackage(getPackageName()); // app-intern
        i.putExtra(EXTRA_STATUS_TEXT, msg);
        sendBroadcast(i);
    }

    private Notification buildNotification(String text) {
        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Video Upstream")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.presence_video_online)
                .setOngoing(true)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.notify(NOTIF_ID, buildNotification(text));
    }

    private void createNotifChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID,
                    "Video Service",
                    NotificationManager.IMPORTANCE_LOW
            );
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            nm.createNotificationChannel(ch);
        }
    }
}
