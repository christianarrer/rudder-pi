package biz.schrottplatz.rudderpi;

import static android.content.Context.MODE_PRIVATE;

import android.content.Context;
import android.util.Log;

import fi.iki.elonen.NanoHTTPD;
import android.content.SharedPreferences;

import java.util.HashMap;
import java.util.Map;

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

        if ("/rudder-pi/ip".equals(session.getUri()) && Method.POST.equals(session.getMethod())) {

            Map<String, String> files = new HashMap<>();
            try {
                session.parseBody(files);
            } catch (Exception e) {
                return newFixedLengthResponse(
                        Response.Status.INTERNAL_ERROR,
                        "application/json",
                        "{\"ok\":false,\"error\":\"parse_body_failed\"}"
                );
            }

            String body = files.get("postData");
            if (body == null || body.isBlank()) {
                return newFixedLengthResponse(
                        Response.Status.BAD_REQUEST,
                        "application/json",
                        "{\"ok\":false,\"error\":\"empty_body\"}"
                );
            }

            String ip;
            try {
                org.json.JSONObject json = new org.json.JSONObject(body);
                ip = json.optString("ip", null);
            } catch (Exception e) {
                return newFixedLengthResponse(
                        Response.Status.BAD_REQUEST,
                        "application/json",
                        "{\"ok\":false,\"error\":\"invalid_json\"}"
                );
            }

            if (ip == null || ip.isBlank()) {
                return newFixedLengthResponse(
                        Response.Status.BAD_REQUEST,
                        "application/json",
                        "{\"ok\":false,\"error\":\"missing_ip\"}"
                );
            }

            try {
                java.net.InetAddress.getByName(ip);
            } catch (Exception e) {
                return newFixedLengthResponse(
                        Response.Status.BAD_REQUEST,
                        "application/json",
                        "{\"ok\":false,\"error\":\"invalid_ip\",\"value\":\"" + ip + "\"}"
                );
            }

            android.content.SharedPreferences prefs =
                    telemetryService.getSharedPreferences("app", android.content.Context.MODE_PRIVATE);

            prefs.edit()
                    .putString("rtsp_remote_server", ip)
                    .commit();

            return newFixedLengthResponse(
                    Response.Status.OK,
                    "application/json",
                    "{\"ok\":true,\"rtsp_remote_server\":\"" + ip + "\"}"
            );
        }

        return newFixedLengthResponse(Response.Status.NOT_FOUND, "application/json",
                "{\"ok\":false,\"error\":\"not_found\"}");
    }
}


