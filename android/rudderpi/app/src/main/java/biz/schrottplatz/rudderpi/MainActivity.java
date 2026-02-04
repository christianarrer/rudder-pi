package biz.schrottplatz.rudderpi;

import static biz.schrottplatz.rudderpi.util.NetUtil.isValidIPv4;
import static biz.schrottplatz.rudderpi.util.NetUtil.isValidTcpPort;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;

import android.text.InputFilter;
import android.text.Layout;
import android.text.method.ScrollingMovementMethod;
import android.util.Log;
import android.view.View;

import androidx.core.content.ContextCompat;

import android.widget.Button;
import android.widget.TextView;
import android.widget.EditText;

import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Date;
import java.util.Deque;
import java.util.Locale;


public class MainActivity extends AppCompatActivity {

    private ActivityResultLauncher<String> permLauncher;
    private final ArrayDeque<String> permQueue = new ArrayDeque<>();
    private boolean permFlowRunning = false;
    private TextView tvStatus;
    private EditText inpHTTPServerXAuthHeaderPassword;
    private EditText inpRtspRemoteServerIP4;
    private EditText inpRtspRemoteServerPort;
    private Button btnApplySettings;
    private static final int MAX_LINES = 200;
    private final Deque<String> statusLines = new ArrayDeque<>();

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (VideoService.ACTION_STATUS.equals(intent.getAction())) {
                String msg = intent.getStringExtra(VideoService.EXTRA_STATUS_TEXT);
                addStatusLine(msg);
            }
        }
    };

    private void addStatusLine(String msg) {
        String ts = new SimpleDateFormat("HH:mm:ss", Locale.getDefault())
                .format(new Date());

        statusLines.addFirst("[" + ts + "] " + msg);

        while (statusLines.size() > MAX_LINES) {
            statusLines.removeLast();
        }

        StringBuilder sb = new StringBuilder();
        for (String line : statusLines) {
            sb.append(line).append('\n');
        }

        tvStatus.setText(sb.toString());
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvStatus = findViewById(R.id.tvStatus);
        tvStatus.setMovementMethod(new ScrollingMovementMethod());

        inpHTTPServerXAuthHeaderPassword = findViewById(R.id.inpHTTPServerXAuthHeaderPassword);
        inpRtspRemoteServerIP4 = findViewById(R.id.inpRtspRemoteServerIP4);
        inpRtspRemoteServerPort = findViewById(R.id.inpRtspRemoteServerPort);

        btnApplySettings = findViewById(R.id.btnApplySettings);

        var prefs = getSharedPreferences("app", MODE_PRIVATE);

        String pw = prefs.getString("http_server_xauth_header_password", "rudderpi");
        if (!pw.isEmpty()) {
            inpHTTPServerXAuthHeaderPassword.setText(pw);
        }

        String ip = prefs.getString("rtsp_remote_server_ipv4", "192.168.0.1");
        if (!ip.isEmpty()) {
            inpRtspRemoteServerIP4.setText(ip);
        }

        int port = prefs.getInt("rtsp_remote_server_port", 8554);
        inpRtspRemoteServerPort.setText(String.valueOf(port));

        btnApplySettings.setOnClickListener(v -> {
            String pwStr = inpHTTPServerXAuthHeaderPassword.getText().toString().trim();
            String inpIp = inpRtspRemoteServerIP4.getText().toString().trim();
            String portStr = inpRtspRemoteServerPort.getText().toString().trim();

            boolean ok = true;

            if (pwStr.isEmpty()) {
                inpHTTPServerXAuthHeaderPassword.setError("Required");
                ok = false;
            }
            if (!isValidIPv4(inpIp)) {
                inpRtspRemoteServerIP4.setError("Invalid IPv4-Address");
                ok = false;
            } else {
                inpRtspRemoteServerIP4.setError(null);
            }

            if (!isValidTcpPort(portStr)) {
                inpRtspRemoteServerPort.setError("Invalid TCP/UDP-Port (1–65535)");
                ok = false;
            } else {
                inpRtspRemoteServerPort.setError(null);
            }

            if (!ok) return;

            int inpPort = Integer.parseInt(portStr);

            boolean saved = getSharedPreferences("app", MODE_PRIVATE)
                    .edit()
                    .putString("http_server_xauth_header_password", pwStr)
                    .putString("rtsp_remote_server_ipv4", inpIp)
                    .putInt("rtsp_remote_server_port", inpPort)
                    .commit(); // <- synchron, Force-Stop-sicher

            if (!saved) {
                Log.e("UI", "cannot save settings");
            }
        });

        restoreLastStatus();
        registerStatusReceiver();

        permLauncher = registerForActivityResult(
                new ActivityResultContracts.RequestPermission(),
                granted -> {
                    // Optional: wenn abgelehnt -> Hinweis anzeigen, aber trotzdem fortsetzen,
                    // sonst hängt man fest.
                    requestNextPermissionOrStart();
                }
        );

        // Beim ersten Start einmal sauber abarbeiten:
        runPermissionFlow();
    }

    private void runPermissionFlow() {
        if (permFlowRunning) return;
        permFlowRunning = true;

        permQueue.clear();

        // Reihenfolge festlegen:
        // 1) Notifications (Android 13+)
        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                permQueue.add(Manifest.permission.POST_NOTIFICATIONS);
            }
        }

        // 2) Location
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            permQueue.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }

        // 3) Camera
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            permQueue.add(Manifest.permission.CAMERA);
        }

        requestNextPermissionOrStart();
    }

    private void requestNextPermissionOrStart() {
        // nächste fehlende Permission suchen (Queue kann inzwischen veraltet sein)
        while (!permQueue.isEmpty()) {
            String p = permQueue.removeFirst();
            if (ContextCompat.checkSelfPermission(this, p) != PackageManager.PERMISSION_GRANTED) {
                permLauncher.launch(p);
                return; // wichtig: immer nur EIN Dialog gleichzeitig
            }
        }

        // Fertig: alle abgearbeitet
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
        if (hasCamera) startVideoService();
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

    private void stopTelemetryService() {
        Intent i = new Intent(this, TelemetryService.class);
        i.setAction(TelemetryService.ACTION_STOP);
        startService(i);
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

    private void stopVideoService() {
        Intent i = new Intent(this, VideoService.class);
        i.setAction(VideoService.ACTION_STOP);
        startService(i);
    }

    private void registerStatusReceiver() {
        IntentFilter f = new IntentFilter(VideoService.ACTION_STATUS);
        if (android.os.Build.VERSION.SDK_INT >= 33) { // Android 13+
            registerReceiver(statusReceiver, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, f);
        }
    }

    private void restoreLastStatus() {
        SharedPreferences prefs =
                getSharedPreferences(VideoService.PREFS_NAME, MODE_PRIVATE);

        String lastStatus = prefs.getString(
                VideoService.PREF_LAST_STATUS,
                "waiting for status..."
        );

        tvStatus.setText(lastStatus);
    }

    protected void onDestroy() {
        super.onDestroy();
        try {
            unregisterReceiver(statusReceiver);
        } catch (IllegalArgumentException ignored) {}
    }
}