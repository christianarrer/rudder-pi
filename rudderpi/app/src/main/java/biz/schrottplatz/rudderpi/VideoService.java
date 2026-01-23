package biz.schrottplatz.rudderpi;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import com.pedro.rtplibrary.rtsp.RtspCamera2;
import com.pedro.rtsp.utils.ConnectCheckerRtsp;
import com.pedro.rtsp.rtsp.RtspClient;
import com.pedro.rtsp.utils.ConnectCheckerRtsp;

public class VideoService extends Service {

    public static final String ACTION_START = "biz.schrottplatz.rudderpi.action.VIDEO_START";
    public static final String ACTION_STOP  = "biz.schrottplatz.rudderpi.action.VIDEO_STOP";

    public static final String EXTRA_RTSP_URL = "extra_rtsp_url";

    public static final String ACTION_STATUS = "biz.schrottplatz.rudderpi.action.VIDEO_STATUS";
    public static final String EXTRA_STATUS_TEXT = "extra_status_text";

    private static final int NOTIF_ID = 1001;
    private static final String CHANNEL_ID = "video_service";

    private volatile boolean running = false;

    private RtspCamera2 rtspCamera;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotifChannel();
        postStatus("VideoService: created");
        // TODO: init camera/encoder resources if needed
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || intent.getAction() == null) return START_STICKY;

        String action = intent.getAction();
        if (ACTION_START.equals(action)) {
            String url = intent.getStringExtra(EXTRA_RTSP_URL);
            startForeground(NOTIF_ID, buildNotification("Streaming: starting..."));
            startStreaming(url);
        } else if (ACTION_STOP.equals(action)) {
            stopStreaming();
            stopForeground(true);
            stopSelf();
        }

        return START_STICKY;
    }

    private synchronized void startStreaming(String rtspUrl) {
        if (running) {
            postStatus("VideoService: already running");
            return;
        }
        running = true;

        postStatus("VideoService: starting stream to " + rtspUrl);

        if(rtspCamera != null) return;
        rtspCamera = new RtspCamera2(getApplicationContext(), false, new ConnectCheckerRtsp() {
            @Override
            public void onConnectionStartedRtsp(String rtspUrl) {
                Log.i("UPSTREAM", "Connection started on " + rtspUrl);
            }

            @Override
            public void onConnectionSuccessRtsp() {
                Log.i("UPSTREAM", "Connection success");
            }

            @Override
            public void onConnectionFailedRtsp(String reason) {
                Log.e("UPSTREAM", "Connection failed: " + reason);
            }

            @Override
            public void onNewBitrateRtsp(long bitrate) {
                Log.i("UPSTREAM", "New bitrate: " + bitrate);
            }

            @Override
            public void onDisconnectRtsp() {
                Log.i("UPSTREAM", "Disconnected");
            }

            @Override
            public void onAuthErrorRtsp() {
                Log.e("UPSTREAM", "Auth error");
            }

            @Override
            public void onAuthSuccessRtsp() {
                Log.i("UPSTREAM", "Auth success");
            }
        });
        boolean okVideo = rtspCamera.prepareVideo(
                1280,
                720,
                30,
                2000 * 1024,
                0
        );
        if (okVideo) {
            rtspCamera.startPreview();
            rtspCamera.startStream("rtsp://192.168.10.36:8554/ship");
        } else {
            Log.e("UPSTREAM", "This device cant init encoders, this could be for 2 reasons: The encoder selected doesnt support any configuration setted or your device hasnt a H264 or AAC encoder (in this case you can see log error valid encoder not found)");
        }


        // Update notification text
        updateNotification("Streaming: ON");
    }

    private synchronized void stopStreaming() {
        if (!running) return;
        running = false;

        postStatus("VideoService: stopping stream...");

        // camera upstream
        if(rtspCamera != null) {
            if (rtspCamera.isStreaming()) rtspCamera.stopStream();
            if (rtspCamera.isOnPreview()) rtspCamera.stopPreview();
            rtspCamera = null;
        }

        postStatus("VideoService: stopped");
        updateNotification("Streaming: OFF");
    }

    private void postStatus(String msg) {
        Intent i = new Intent(ACTION_STATUS);
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

    @Override
    public void onDestroy() {
        stopStreaming();
        postStatus("VideoService: destroyed");
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null; // started service, no binding
    }
}
