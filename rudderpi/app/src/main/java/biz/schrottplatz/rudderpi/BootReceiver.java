package biz.schrottplatz.rudderpi;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import androidx.core.content.ContextCompat;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        android.util.Log.i("RUDDERPI_BOOT", "received: " + (intent != null ? intent.getAction() : "null"));
        String a = intent != null ? intent.getAction() : "";
        if (Intent.ACTION_BOOT_COMPLETED.equals(a)
                || Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(a)) {

            Intent svc = new Intent(context, TelemetryService.class);
            ContextCompat.startForegroundService(context, svc);
        }
    }
}
