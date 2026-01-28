package biz.schrottplatz.rudderpi;

import android.util.Log;

import fi.iki.elonen.NanoHTTPD;

public class HttpServer extends NanoHTTPD {

    private final TelemetryService telemetryService;
    private final String authToken; // optional

    public HttpServer(int port, TelemetryService svc, String authToken) {
        super(port);
        this.telemetryService = svc;
        this.authToken = authToken;
    }

    private boolean isAuthorized(IHTTPSession session) {
        if (authToken == null || authToken.isEmpty()) return true;
        String h = session.getHeaders().get("x-auth");
        return authToken.equals(h);
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


