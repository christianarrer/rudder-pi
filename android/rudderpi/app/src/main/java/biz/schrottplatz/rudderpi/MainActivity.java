package biz.schrottplatz.rudderpi;

import static biz.schrottplatz.rudderpi.NetUtil.isValidIPv4;
import static biz.schrottplatz.rudderpi.NetUtil.isValidTcpPort;
import static biz.schrottplatz.rudderpi.NetUtil.isValidHostname;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;

import android.os.IBinder;
import android.text.method.ScrollingMovementMethod;
import android.util.Log;
import android.view.SurfaceHolder;
import android.widget.Button;

import androidx.core.content.ContextCompat;

import com.pedro.rtplibrary.rtsp.RtspCamera2;
import com.pedro.rtplibrary.view.OpenGlView;
import com.pedro.rtsp.utils.ConnectCheckerRtsp;

import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Date;
import java.util.Deque;
import java.util.Locale;

import biz.schrottplatz.rudderpi.databinding.ActivityMainBinding;

public class MainActivity extends AppCompatActivity {

    private static final int MAX_LINES = 200;

    private ActivityMainBinding binding;
    private SharedPreferences prefs;

    private ActivityResultLauncher<String> permLauncher;
    private final ArrayDeque<String> permQueue = new ArrayDeque<>();
    private boolean permFlowRunning = false;

    private final Deque<String> statusLines = new ArrayDeque<>();

    private VideoService videoService;
    private boolean serviceBound = false;

    private RtspCamera2 rtspCamera;
    private ConnectCheckerRtsp connectChecker;
    private volatile boolean glSurfaceReady = false;

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (VideoService.ACTION_STATUS.equals(intent.getAction())) {
                String msg = intent.getStringExtra(VideoService.EXTRA_STATUS_TEXT);
                addStatusLine(msg);
            }
        }
    };

    private final SharedPreferences.OnSharedPreferenceChangeListener prefListener =
            (sharedPreferences, key) -> {
                if ("rtsp_remote_server".equals(key)) {
                    final String ip = sharedPreferences.getString("rtsp_remote_server", "");
                    runOnUiThread(() -> binding.inpRtspRemoteServerIP4.setText(ip));
                }
            };

    private final ServiceConnection conn = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            videoService = ((VideoService.LocalBinder) service).getService();
            serviceBound = true;

            // Create checker that forwards to the service
            connectChecker = new ServiceConnectCheckerRtsp(videoService);

            // Only create/attach camera when the Surface is actually valid.
            maybeCreateAndAttachCamera();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            serviceBound = false;
            videoService = null;
            connectChecker = null;
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        binding = ActivityMainBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        // IMPORTANT: OpenGlView must have a valid Surface before RtspCamera2/GL thread can start.
        binding.glView.getHolder().addCallback(new SurfaceHolder.Callback() {
            @Override
            public void surfaceCreated(SurfaceHolder holder) {
                Log.i("rudderpi", "surfaceCreated: valid=" + holder.getSurface().isValid()
                        + " viewShown=" + binding.glView.isShown()
                        + " vis=" + binding.glView.getVisibility()
                        + " w=" + binding.glView.getWidth() + " h=" + binding.glView.getHeight());
                glSurfaceReady = holder.getSurface() != null && holder.getSurface().isValid();
                Log.i("MainActivity", "GL surfaceCreated valid=" + glSurfaceReady);

                // Notify service (it will gate reconnect attempts until ready).
                sendVideoServiceAction(VideoService.ACTION_GL_SURFACE_READY);

                // If service is already bound, create/attach camera now.
                maybeCreateAndAttachCamera();
            }

            @Override
            public void surfaceDestroyed(SurfaceHolder holder) {
                glSurfaceReady = false;
                Log.i("MainActivity", "GL surfaceDestroyed");

                // Tell service to stop streaming and forget GL.
                sendVideoServiceAction(VideoService.ACTION_GL_SURFACE_GONE);

                // Detach and drop camera instance (GL context is gone).
                detachAndDropCamera();
            }

            @Override
            public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { }
        });

        binding.tvStatus.setMovementMethod(new ScrollingMovementMethod());

        prefs = getSharedPreferences("app", MODE_PRIVATE);

        // Restore UI fields
        String pw = prefs.getString("http_server_xauth_header_password", "rudderpi");
        if (pw != null && !pw.isEmpty()) binding.inpHTTPServerXAuthHeaderPassword.setText(pw);

        String host = prefs.getString("rtsp_remote_server", "rudder-pi.local");
        if (host != null && !host.isEmpty()) binding.inpRtspRemoteServerIP4.setText(host);

        int port = prefs.getInt("rtsp_remote_server_port", 8554);
        binding.inpRtspRemoteServerPort.setText(String.valueOf(port));

        binding.btnApplySettings.setOnClickListener(v -> {
            String pwStr = binding.inpHTTPServerXAuthHeaderPassword.getText().toString().trim();
            String inpHost = binding.inpRtspRemoteServerIP4.getText().toString().trim();
            String portStr = binding.inpRtspRemoteServerPort.getText().toString().trim();

            boolean ok = true;

            if (pwStr.isEmpty()) {
                binding.inpHTTPServerXAuthHeaderPassword.setError("Required");
                ok = false;
            }

            if (!isValidIPv4(inpHost) && !isValidHostname(inpHost)) {
                binding.inpRtspRemoteServerIP4.setError("Invalid IPv4-Address/Hostname");
                ok = false;
            } else {
                binding.inpRtspRemoteServerIP4.setError(null);
            }

            if (!isValidTcpPort(portStr)) {
                binding.inpRtspRemoteServerPort.setError("Invalid TCP/UDP-Port (1–65535)");
                ok = false;
            } else {
                binding.inpRtspRemoteServerPort.setError(null);
            }

            if (!ok) return;

            int inpPort = Integer.parseInt(portStr);

            boolean saved = getSharedPreferences("app", MODE_PRIVATE)
                    .edit()
                    .putString("http_server_xauth_header_password", pwStr)
                    .putString("rtsp_remote_server", inpHost)
                    .putInt("rtsp_remote_server_port", inpPort)
                    .commit();

            if (!saved) {
                Log.e("UI", "cannot save settings");
            } else {
                addStatusLine("Settings saved");
                // Wake reconnect thread if service is bound
                if (serviceBound && videoService != null) {
                    videoService.kickReconnectNow();
                }
            }
        });

        Button btnAdmin = findViewById(R.id.btnAdmin);
        btnAdmin.setOnClickListener(v -> {
            // Open tethering settings (may fall back depending on OEM/Android version)
            Intent i = new Intent(android.provider.Settings.ACTION_WIRELESS_SETTINGS);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                startActivity(i);
            } catch (Exception e) {
                // Fallback: open general settings
                Intent fallback = new Intent(android.provider.Settings.ACTION_SETTINGS);
                fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(fallback);
            }
        });

        restoreLastStatus();
        registerStatusReceiver();

        permLauncher = registerForActivityResult(
                new ActivityResultContracts.RequestPermission(),
                granted -> requestNextPermissionOrStart()
        );

        runPermissionFlow();
    }

    @Override
    protected void onResume() {
        super.onResume();
        prefs.registerOnSharedPreferenceChangeListener(prefListener);
    }

    @Override
    protected void onPause() {
        prefs.unregisterOnSharedPreferenceChangeListener(prefListener);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();

        try {
            unregisterReceiver(statusReceiver);
        } catch (IllegalArgumentException ignored) {}

        detachAndDropCamera();

        if (serviceBound) {
            try {
                unbindService(conn);
            } catch (Exception ignored) {}
            serviceBound = false;
        }
    }

    private void addStatusLine(String msg) {
        String ts = new SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(new Date());
        statusLines.addFirst("[" + ts + "] " + msg);

        while (statusLines.size() > MAX_LINES) statusLines.removeLast();

        StringBuilder sb = new StringBuilder();
        for (String line : statusLines) sb.append(line).append('\n');

        binding.tvStatus.setText(sb.toString());
    }

    private void runPermissionFlow() {
        if (permFlowRunning) return;
        permFlowRunning = true;

        permQueue.clear();

        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                permQueue.add(Manifest.permission.POST_NOTIFICATIONS);
            }
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            permQueue.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            permQueue.add(Manifest.permission.CAMERA);
        }

        requestNextPermissionOrStart();
    }

    private void requestNextPermissionOrStart() {
        while (!permQueue.isEmpty()) {
            String p = permQueue.removeFirst();
            if (ContextCompat.checkSelfPermission(this, p) != PackageManager.PERMISSION_GRANTED) {
                permLauncher.launch(p);
                return;
            }
        }

        permFlowRunning = false;
        maybeStartServices();
    }

    private void maybeStartServices() {
        boolean hasLocation =
                ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                        == PackageManager.PERMISSION_GRANTED;

        boolean hasCamera =
                ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                        == PackageManager.PERMISSION_GRANTED;

        if (hasLocation) startTelemetryService();

        if (hasCamera) {
            startVideoService();

            // Bind so we can attach the GL camera instance to the service.
            bindService(new Intent(this, VideoService.class), conn, BIND_AUTO_CREATE);
        }
    }

    private void startTelemetryService() {
        Intent i = new Intent(this, TelemetryService.class);
        i.setAction(TelemetryService.ACTION_START);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(i);
        } else {
            startService(i);
        }
    }

    private void startVideoService() {
        Intent i = new Intent(this, VideoService.class);
        i.setAction(VideoService.ACTION_START);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(i);
        } else {
            startService(i);
        }
    }

    private void sendVideoServiceAction(String action) {
        Log.i("rudderpi", "sendVideoServiceAction action: " + VideoService.ACTION_GL_SURFACE_READY);
        try {
            Intent i = new Intent(this, VideoService.class);
            i.setAction(action);
            // Service should already be running; startService is fine for control actions.
            startService(i);
        } catch (Exception e) {
            Log.w("MainActivity", "sendVideoServiceAction failed: " + action, e);
        }
    }

    private void maybeCreateAndAttachCamera() {
        if (!serviceBound || videoService == null) return;
        if (!glSurfaceReady) return;
        if (rtspCamera != null) return;
        if (connectChecker == null) return;

        try {
            // Create a fresh RtspCamera2 bound to the current Surface.
            rtspCamera = new RtspCamera2(binding.glView, connectChecker);

            boolean okVideo = rtspCamera.prepareVideo(720, 1280, 30, 1500 * 1024, 90);
            if (!okVideo) {
                Log.e("MainActivity", "prepareVideo failed");
                addStatusLine("RTSP: encoder init failed");
                rtspCamera = null;
                return;
            }

            // Real pixel rotation via GL
            rtspCamera.getGlInterface().setRotation(90);

            // Hand camera to service for reconnect/start/stop logic
            videoService.attachCamera(rtspCamera);
        } catch (Exception e) {
            Log.e("MainActivity", "maybeCreateAndAttachCamera failed", e);
            addStatusLine("RTSP: camera attach failed: " + e.getMessage());
            rtspCamera = null;
        }
    }

    private void detachAndDropCamera() {
        try {
            if (serviceBound && videoService != null && rtspCamera != null) {
                videoService.detachCamera(rtspCamera);
            }
        } catch (Exception ignored) {
        } finally {
            rtspCamera = null;
        }
    }

    private void registerStatusReceiver() {
        IntentFilter f = new IntentFilter(VideoService.ACTION_STATUS);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, f);
        }
    }

    private void restoreLastStatus() {
        SharedPreferences p = getSharedPreferences(VideoService.PREFS_NAME, MODE_PRIVATE);
        String lastStatus = p.getString(VideoService.PREF_LAST_STATUS, "waiting for status...");
        binding.tvStatus.setText(lastStatus);
    }
}
