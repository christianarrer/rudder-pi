package biz.schrottplatz.rudderpi;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import androidx.core.content.ContextCompat;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String a = intent != null ? intent.getAction() : "null";
        Log.i("RUDDERPI_BOOT", "received: " + a);

        if (Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(a)) {
            // Zu früh: user noch locked → Services starten ist je nach Gerät/ROM unzuverlässig
            return;
        }

        // BOOT_COMPLETED oder USER_PRESENT
        Intent svc = new Intent(context, TelemetryService.class);
        ContextCompat.startForegroundService(context, svc);
    }
}
