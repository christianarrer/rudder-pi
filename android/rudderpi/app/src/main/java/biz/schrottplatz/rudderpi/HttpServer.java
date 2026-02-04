package biz.schrottplatz.rudderpi;

import static android.content.Context.MODE_PRIVATE;

import android.content.Context;
import android.util.Log;

import fi.iki.elonen.NanoHTTPD;
import android.content.SharedPreferences;

public class HttpServer extends NanoHTTPD {

    private final TelemetryService telemetryService;


    public HttpServer(int port, TelemetryService svc) {
        super(port);
        this.telemetryService = svc;
    }

    private boolean isAuthorized(IHTTPSession session) {
        if (telemetryService == null) return false; // oder true (aber dann offen!)
        String expected = telemetryService.getSharedPreferences("app", Context.MODE_PRIVATE)
                .getString("http_server_xauth_header_password", "");
        if (expected == null || expected.isEmpty()) return false;
        String got = session.getHeaders().get("x-auth");
        return got != null && expected.equals(got);
    }

    @Override
    public Response serve(IHTTPSession session) {
        // optional auth
        if (!isAuthorized(session)) {
            return newFixedLengthResponse(Response.Status.UNAUTHORIZED, "application/json",
                    "{\"ok\":false,\"error\":\"unauthorized\"}");
        }

        //Log.d("THREAD", "serve() on " + Thread.currentThread().getName());
        if ("/health".equals(session.getUri())) {
            return newFixedLengthResponse(
                    Response.Status.OK,
                    "application/json",
                    "{\"ok\":true}"
            );
        }

        if ("/state.json".equals(session.getUri())) {
            String json = TelemetryState.get().toJson();
            return newFixedLengthResponse(
                    Response.Status.OK,
                    "application/json",
                    json
            );
        }

        // --- VIDEO CONTROL ---
        if ("/video/start".equals(session.getUri()) && Method.POST.equals(session.getMethod())) {
            telemetryService.requestStartVideo();
            return newFixedLengthResponse(Response.Status.OK, "application/json",
                    "{\"ok\":true,\"video\":\"starting\"}");
        }

        if ("/video/stop".equals(session.getUri()) && Method.POST.equals(session.getMethod())) {
            telemetryService.requestStopVideo();
            return newFixedLengthResponse(Response.Status.OK, "application/json",
                    "{\"ok\":true,\"video\":\"stopping\"}");
        }

        if ("/video/status".equals(session.getUri()) && Method.GET.equals(session.getMethod())) {
            boolean running = telemetryService.isVideoRunning();
            return newFixedLengthResponse(Response.Status.OK, "application/json",
                    "{\"ok\":true,\"running\":" + running + "}");
        }

        return newFixedLengthResponse(Response.Status.NOT_FOUND, "application/json",
                "{\"ok\":false,\"error\":\"not_found\"}");
    }
}


