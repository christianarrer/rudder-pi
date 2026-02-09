package biz.schrottplatz.rudderpi;

import static biz.schrottplatz.rudderpi.NetUtil.isValidHostname;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.core.app.NotificationCompat;

import com.pedro.rtplibrary.rtsp.RtspCamera2;

public class VideoService extends Service {

    public static final String PREFS_NAME = "video_service_prefs";
    public static final String PREF_LAST_STATUS = "last_status";

    public static final String ACTION_START = "biz.schrottplatz.rudderpi.action.VIDEO_START";
    public static final String ACTION_STOP  = "biz.schrottplatz.rudderpi.action.VIDEO_STOP";

    public static final String ACTION_STATUS = "biz.schrottplatz.rudderpi.action.VIDEO_STATUS";
    public static final String EXTRA_STATUS_TEXT = "extra_status_text";

    private static final int NOTIF_ID = 1001;
    private static final String CHANNEL_ID = "video_service";
    private static final String TAG = "VideoService";

    // Fixed RTSP path
    private static final String RTSP_PATH = "/rudderpiraw";

    private final Object cameraLock = new Object();
    private RtspCamera2 rtspCamera; // set by Activity via attachCamera()

    private volatile boolean serviceRunning = false;
    private volatile boolean streamRunning  = false;
    private volatile boolean wantReconnect  = false;

    private final Object stateLock = new Object();

    private Thread rtspThread;
    private int backoffMs = 1000;
    private static final int BACKOFF_MAX_MS = 30_000;

    private volatile String rtspRemoteServerIP4 = "";
    private volatile int rtspRemoteServerPort = 8554;

    private static volatile boolean RUNNING = false;
    public static boolean isRunning() { return RUNNING; }

    // Binder
    public class LocalBinder extends android.os.Binder {
        public VideoService getService() { return VideoService.this; }
    }
    private final IBinder binder = new LocalBinder();

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    // ============================================================
    // RTSP callback targets (called via ServiceConnectCheckerRtsp)
    // ============================================================

    public void onRtspConnectionStarted(String rtspUrl) {
        Log.i(TAG, "RTSP: connection started: " + rtspUrl);
        postStatus("RTSP: connecting...");
    }

    public void onRtspConnectionSuccess() {
        Log.i(TAG, "RTSP: connection success");
        postStatus("RTSP: connected to " + rtspRemoteServerIP4);
        synchronized (stateLock) { stateLock.notifyAll(); }
    }

    public void onRtspConnectionFailed(String reason) {
        Log.e(TAG, "RTSP: connection failed: " + reason);
        postStatus("RTSP failed: " + reason);
        streamRunning = false;
        wantReconnect = true;
        synchronized (stateLock) { stateLock.notifyAll(); }
    }

    public void onRtspNewBitrate(long bitrate) {
        Log.i(TAG, "RTSP: new bitrate: " + bitrate);
    }

    public void onRtspDisconnected() {
        Log.i(TAG, "RTSP: disconnected");
        postStatus("RTSP: disconnected");
        streamRunning = false;
        wantReconnect = true;
        synchronized (stateLock) { stateLock.notifyAll(); }
    }

    public void onRtspAuthError() {
        Log.e(TAG, "RTSP: auth error");
        postStatus("RTSP: auth error");
        streamRunning = false;
        wantReconnect = true;
        synchronized (stateLock) { stateLock.notifyAll(); }
    }

    public void onRtspAuthSuccess() {
        Log.i(TAG, "RTSP: auth success");
        postStatus("RTSP: auth ok");
    }

    // ============================================================
    // Lifecycle
    // ============================================================

    @Override
    public void onCreate() {
        super.onCreate();
        RUNNING = true;

        createNotifChannel();
        startForeground(NOTIF_ID, buildNotification("Starting..."));

        postStatus("VideoService: onCreate()");
        loadRtspSettingsFromPrefs();

        serviceRunning = true;
        startReconnectThreadIfNeeded();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        loadRtspSettingsFromPrefs();
        startReconnectThreadIfNeeded();

        if (intent != null && intent.getAction() != null) {
            String action = intent.getAction();
            if (ACTION_START.equals(action)) {
                postStatus("VideoService: ACTION_START");
                wantReconnect = true;
                kickReconnectNow();
            } else if (ACTION_STOP.equals(action)) {
                postStatus("VideoService: ACTION_STOP");
                wantReconnect = false;
                stopStreaming();
            }
        }

        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        postStatus("VideoService: onDestroy()");
        stopServiceAndThread();
        RUNNING = false;
        super.onDestroy();
    }

    // ============================================================
    // Camera attach/detach from Activity
    // ============================================================

    public void attachCamera(RtspCamera2 camera) {
        synchronized (cameraLock) {
            this.rtspCamera = camera;
        }
        // If the service wants streaming, wake the loop immediately.
        wantReconnect = true;
        kickReconnectNow();
    }

    public void detachCamera(RtspCamera2 camera) {
        synchronized (cameraLock) {
            if (this.rtspCamera == camera) {
                this.rtspCamera = null;
            }
        }
    }

    private RtspCamera2 getCamera() {
        synchronized (cameraLock) {
            return rtspCamera;
        }
    }

    public void kickReconnectNow() {
        synchronized (stateLock) {
            stateLock.notifyAll();
        }
    }

    // ============================================================
    // Thread / loop
    // ============================================================

    private void startReconnectThreadIfNeeded() {
        if (rtspThread != null && rtspThread.isAlive()) return;

        rtspThread = new Thread(this::rtspLoop, "rtsp-reconnect");
        rtspThread.start();
    }

    private void stopServiceAndThread() {
        serviceRunning = false;
        wantReconnect = false;

        synchronized (stateLock) { stateLock.notifyAll(); }

        stopStreaming();
    }

    private void rtspLoop() {
        backoffMs = 1000;

        while (serviceRunning) {
            loadRtspSettingsFromPrefs();

            if ((!NetUtil.isValidIPv4(rtspRemoteServerIP4) && !isValidHostname(rtspRemoteServerIP4))
                    || !NetUtil.isValidTcpPort(rtspRemoteServerPort)) {
                postStatus("RTSP: waiting for valid settings...");
                sleepQuiet(1000);
                continue;
            }

            if (!wantReconnect) {
                waitOnStateLock(30_000);
                continue;
            }

            // Do not clear wantReconnect until we actually can attempt a start.
            RtspCamera2 cam = getCamera();
            if (cam == null) {
                postStatus("RTSP: camera not attached yet (Activity/GL not ready)");
                sleepQuiet(500);
                continue;
            }

            wantReconnect = false;

            String rtspUrl = "rtsp://" + rtspRemoteServerIP4 + ":" + rtspRemoteServerPort + RTSP_PATH;
            postStatus("RTSP: start attempt: " + rtspUrl);

            boolean started = startStreaming(rtspUrl);
            if (!started) {
                postStatus("RTSP: start failed. retry in " + backoffMs + "ms");
                sleepQuiet(backoffMs);
                backoffMs = Math.min(backoffMs * 2, BACKOFF_MAX_MS);
                wantReconnect = true;
                continue;
            }

            // Wait for disconnect/fail callbacks to signal wantReconnect=true.
            waitOnStateLock(60_000);

            if (wantReconnect && serviceRunning) {
                stopStreaming();
                sleepQuiet(500);
            }

            if (!wantReconnect) {
                backoffMs = 1000;
            }
        }

        stopStreaming();
        postStatus("RTSP: loop ended");
    }

    private void waitOnStateLock(long timeoutMs) {
        synchronized (stateLock) {
            try {
                stateLock.wait(timeoutMs);
            } catch (InterruptedException ignored) {}
        }
    }

    private void sleepQuiet(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {}
    }

    // ============================================================
    // Start/Stop streaming
    // ============================================================

    private synchronized boolean startStreaming(String rtspUrl) {
        if (streamRunning) {
            postStatus("VideoService: stream already running");
            return true;
        }

        RtspCamera2 cam = getCamera();
        if (cam == null) {
            postStatus("VideoService: rtspCamera is null");
            return false;
        }

        postStatus("VideoService: starting stream to " + rtspUrl);

        try {
            if (!cam.isOnPreview()) {
                cam.startPreview();
            }

            cam.startStream(rtspUrl);

            streamRunning = true;
            updateNotification("Streaming: ON");
            return true;
        } catch (Exception e) {
            Log.e(TAG, "startStreaming exception", e);
            streamRunning = false;
            safeResetRtspCamera();
            return false;
        }
    }

    private synchronized void safeResetRtspCamera() {
        RtspCamera2 cam = getCamera();
        if (cam == null) return;

        try {
            if (cam.isStreaming()) cam.stopStream();
            if (cam.isOnPreview()) cam.stopPreview();
        } catch (Exception ignored) {}
    }

    private synchronized void stopStreaming() {
        if (!streamRunning) return;

        postStatus("VideoService: stopping stream...");

        RtspCamera2 cam = getCamera();
        try {
            if (cam != null) {
                if (cam.isStreaming()) cam.stopStream();
                if (cam.isOnPreview()) cam.stopPreview();
            }
        } catch (Exception e) {
            Log.w(TAG, "stopStreaming exception", e);
        } finally {
            streamRunning = false;
            updateNotification("Streaming: OFF");
            synchronized (stateLock) { stateLock.notifyAll(); }
        }

        postStatus("VideoService: stopped");
    }

    // ============================================================
    // Settings
    // ============================================================

    private void loadRtspSettingsFromPrefs() {
        String host = getSharedPreferences("app", MODE_PRIVATE)
                .getString("rtsp_remote_server", "rudder-pi.local");

        if (host != null) host = host.trim();
        if (host == null || host.isEmpty()) host = "rudder-pi.local";

        if (NetUtil.isValidIPv4(host) || NetUtil.isValidHostname(host)) {
            rtspRemoteServerIP4 = host;
        } else {
            rtspRemoteServerIP4 = "rudder-pi.local";
        }

        int port = getSharedPreferences("app", MODE_PRIVATE)
                .getInt("rtsp_remote_server_port", 8554);

        if (NetUtil.isValidTcpPort(port)) rtspRemoteServerPort = port;
    }

    // ============================================================
    // Status + Notifications
    // ============================================================

    private void postStatus(String msg) {
        Log.i(TAG, msg);

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit().putString(PREF_LAST_STATUS, msg).apply();

        Intent i = new Intent(ACTION_STATUS);
        i.setPackage(getPackageName());
        i.putExtra(EXTRA_STATUS_TEXT, msg);
        sendBroadcast(i);
    }

    private void createNotifChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;

        NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "Video Service", NotificationManager.IMPORTANCE_LOW
        );

        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(ch);
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
}

