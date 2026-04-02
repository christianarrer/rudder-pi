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
import android.os.SystemClock;
import android.util.Log;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import com.pedro.rtplibrary.rtsp.RtspCamera2;

public class VideoService extends Service {

    private static volatile VideoService INSTANCE;

    private volatile boolean torchEnabled = false;

    public static @Nullable VideoService getInstance() {
        return INSTANCE;
    }

    // Used by rtspLoop() wait/notify mechanism
    private final Object stateLock = new Object();

    // Serializes start/stop to avoid MediaCodec "Running state" crashes
    private final Object streamLock = new Object();

    private enum StreamState { STOPPED, STARTING, STARTED, STOPPING }
    private volatile StreamState streamState = StreamState.STOPPED;

    // Optional: avoid rapid restart loops
    private volatile long lastStartAttemptMs = 0;
    private static final long START_COOLDOWN_MS = 800;

    // GL surface lifecycle actions (sent by MainActivity)
    public static final String ACTION_GL_SURFACE_READY =
            "biz.schrottplatz.rudderpi.action.GL_SURFACE_READY";

    public static final String ACTION_GL_SURFACE_GONE =
            "biz.schrottplatz.rudderpi.action.GL_SURFACE_GONE";

    private boolean glSurfaceReady;
    private volatile long nextAllowedStartMs = 0; // debounce after surfaceCreated

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
        INSTANCE = this;
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

        if (intent != null && intent.getAction() != null) {
            String action = intent.getAction();

            if (ACTION_GL_SURFACE_READY.equals(action)) {
                Log.i(TAG, "GL surface READY");
                glSurfaceReady = true;
                // Debounce: some devices report surfaceCreated before GL/codec pipeline is stable.
                nextAllowedStartMs = SystemClock.uptimeMillis() + 600;
                wantReconnect = true;
                notifyStateLock();
            }

            else if (ACTION_GL_SURFACE_GONE.equals(action)) {
                Log.i(TAG, "GL surface GONE");
                glSurfaceReady = false;
                wantReconnect = false;
                stopStreaming();
            }
        }

        return START_STICKY;

    }

    @Override
    public void onDestroy() {
        postStatus("VideoService: onDestroy()");
        INSTANCE = null;
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
                torchEnabled = false;
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
            // Ensure GL surface is ready before trying to start encoders.
            if (!glSurfaceReady) {
                postStatus("RTSP: waiting for GL surface (Activity not ready / screen locked / surface destroyed)");
                sleepQuiet(250);
                continue;
            }

            // Debounce after GL surface becomes ready.
            long nowMs = SystemClock.uptimeMillis();
            if (nowMs < nextAllowedStartMs) {
                sleepQuiet(100);
                continue;
            }

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

    private boolean startStreaming(String rtspUrl) {
        final RtspCamera2 cam = getCamera();
        if (cam == null) return false;
        if (!glSurfaceReady) return false;

        synchronized (streamLock) {
            long now = SystemClock.uptimeMillis();
            if (now - lastStartAttemptMs < START_COOLDOWN_MS) {
                postStatus("RTSP: start throttled");
                return false;
            }
            lastStartAttemptMs = now;

            // Don't start if we're already starting/started/stopping
            if (streamState == StreamState.STARTING || streamState == StreamState.STARTED || streamState == StreamState.STOPPING) {
                postStatus("RTSP: start ignored (state=" + streamState + ")");
                return false;
            }
            streamState = StreamState.STARTING;
        }

        try {
            // IMPORTANT: ensure any previous encoder session is fully stopped before starting again.
            // Some devices can report isStreaming()==false while MediaCodec is still Running.
            try {
                if (cam.isStreaming()) {
                    stopStreamAndWait(cam, 1500);
                }
            } catch (Exception ignored) { }


            postStatus("VideoService: starting stream to " + rtspUrl);
            cam.startStream(rtspUrl);

            synchronized (streamLock) {
                streamState = StreamState.STARTED;
            }
            return true;

        } catch (Exception e) {
            boolean isEncoderAllocFailure =
                    (e instanceof NullPointerException)
                            || (e.getMessage() != null && e.getMessage().contains("MediaCodec.start()"));

            postStatus("RTSP: startStreaming exception: " + e.getClass().getSimpleName() + " " + e.getMessage());
            Log.e(TAG, "startStreaming exception", e);

            // Always stop/release
            hardStopAndDropCamera();

            // If encoder allocation failed, back off longer (Qualcomm sometimes needs time)
            if (isEncoderAllocFailure) {
                postStatus("RTSP: encoder init failed, cooldown 2000ms");
                sleepQuiet(2000);
            } else {
                sleepQuiet(500);
            }

            wantReconnect = true;
            return false;
        }
    }

    private void hardStopAndDropCamera() {
        try {
            RtspCamera2 cam = getCamera();
            if (cam != null) {
                try {
                    if (cam.isStreaming()) cam.stopStream();
                } catch (Exception ignored) { }
            }
        } finally {
            // IMPORTANT: drop the instance so Activity will attach a fresh one on next surfaceCreated
            RtspCamera2 cam = getCamera();
            if (cam != null) {
                detachCamera(cam);
            }
            sleepQuiet(200);
        }
    }

    private void stopStreamAndWait(RtspCamera2 cam, long timeoutMs) {
        if (cam == null) return;

        try {
            if (cam.isStreaming()) {
                cam.stopStream();
            } else {
                return;
            }
        } catch (Exception ignored) {
            return;
        }

        long end = SystemClock.uptimeMillis() + timeoutMs;
        while (SystemClock.uptimeMillis() < end) {
            try {
                if (!cam.isStreaming()) break;
            } catch (Exception ignored) {
                break;
            }
            sleepQuiet(50);
        }
    }

    private synchronized void safeResetRtspCamera() {
        RtspCamera2 cam = getCamera();
        if (cam == null) return;

        try {
            stopStreamAndWait(cam, 1500);
            if (cam.isOnPreview()) cam.stopPreview();
        } catch (Exception ignored) {}
    }

    private void stopStreaming() {
        RtspCamera2 cam = getCamera();

        synchronized (streamLock) {
            if (streamState == StreamState.STOPPED || streamState == StreamState.STOPPING) return;
            streamState = StreamState.STOPPING;
        }

        try {
            if (cam != null) {
                postStatus("RTSP: stopping stream...");
                stopStreamAndWait(cam, 1500);
            }
        } catch (Exception e) {
            Log.w(TAG, "stopStreaming exception", e);
        } finally {
            synchronized (streamLock) {
                streamState = StreamState.STOPPED;
            }
        }
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

    private void notifyStateLock() {
        synchronized (stateLock) {
            stateLock.notifyAll();
        }
    }

    public synchronized boolean setTorchEnabled(boolean enabled) {
        RtspCamera2 cam = getCamera();
        if (cam == null) {
            Log.w(TAG, "Torch: camera not attached");
            return false;
        }

        try {
            if (enabled) {
                cam.enableLantern();
                torchEnabled = true;
                postStatus("Torch: on");
            } else {
                cam.disableLantern();
                torchEnabled = false;
                postStatus("Torch: off");
            }
            return true;
        } catch (Exception e) {
            Log.e(TAG, "Torch toggle failed", e);
            postStatus("Torch failed: " + e.getMessage());
            return false;
        }
    }

    public synchronized boolean isTorchEnabled() {
        return torchEnabled;
    }
}




