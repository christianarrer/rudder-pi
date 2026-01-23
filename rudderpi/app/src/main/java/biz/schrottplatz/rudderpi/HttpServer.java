package biz.schrottplatz.rudderpi;

import android.util.Log;

import fi.iki.elonen.NanoHTTPD;

public class HttpServer extends NanoHTTPD {

    public HttpServer(int port) {
        super(port);
    }

    @Override
    public Response serve(IHTTPSession session) {
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

        return newFixedLengthResponse(
                Response.Status.NOT_FOUND,
                "text/plain",
                "not found"
        );
    }
}


