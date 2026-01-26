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
import android.util.Log;
import android.view.View;

import androidx.core.content.ContextCompat;

import android.widget.Button;
import android.widget.TextView;
import android.widget.EditText;


public class MainActivity extends AppCompatActivity {

    private ActivityResultLauncher<String> fineLocPerm;
    private ActivityResultLauncher<String> camPerm;
    private TextView tvStatus;
    private Button btnStart;
    private Button btnStop;
    private InputFilter filterP4Address;
    private InputFilter filterTcpPort;
    private EditText inpRtspRemoteServerIP4;
    private EditText inpRtspRemoteServerPort;
    private Button btnApplySettings;

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (VideoService.ACTION_STATUS.equals(intent.getAction())) {
                String msg = intent.getStringExtra(VideoService.EXTRA_STATUS_TEXT);
                tvStatus.setText(msg);
            }
        }
    };


    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvStatus = findViewById(R.id.tvStatus);

        inpRtspRemoteServerIP4 = findViewById(R.id.inpRtspRemoteServerIP4);
        inpRtspRemoteServerPort = findViewById(R.id.inpRtspRemoteServerPort);

        btnApplySettings = findViewById(R.id.btnApplySettings);

        /*
        btnStart = findViewById(R.id.btnStart);
        btnStop  = findViewById(R.id.btnStop);
         */

        var prefs = getSharedPreferences("app", MODE_PRIVATE);

        String ip = prefs.getString("rtsp_remote_server_ipv4", "");
        if (!ip.isEmpty()) {
            inpRtspRemoteServerIP4.setText(ip);
        }

        int port = prefs.getInt("rtsp_remote_server_port", 8554);
        inpRtspRemoteServerPort.setText(String.valueOf(port));

        btnApplySettings.setOnClickListener(v -> {
            String inpIp = inpRtspRemoteServerIP4.getText().toString().trim();
            String portStr = inpRtspRemoteServerPort.getText().toString().trim();

            boolean ok = true;

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
                    .putString("rtsp_remote_server_ipv4", inpIp)
                    .putInt("rtsp_remote_server_port", inpPort)
                    .commit(); // <- synchron, Force-Stop-sicher

            if (!saved) {
                Log.e("UI", "cannot save settings");
            }
        });

        restoreLastStatus();
        registerStatusReceiver();

        fineLocPerm = registerForActivityResult(
                new ActivityResultContracts.RequestPermission(),
                granted -> startTelemetryService()
        );

        camPerm = registerForActivityResult(
                new ActivityResultContracts.RequestPermission(),
                granted -> startVideoService()
        );

        if((ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED) && (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED)) {
            startTelemetryService();
            startVideoService();
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            fineLocPerm.launch(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        if(ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            camPerm.launch(Manifest.permission.CAMERA);
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