package biz.schrottplatz.rudderpi;

import com.pedro.rtsp.utils.ConnectCheckerRtsp;

/**
 * Forwards RTSP callbacks to VideoService.
 * Keeps all reconnect/state logic inside the service.
 */
public final class ServiceConnectCheckerRtsp implements ConnectCheckerRtsp {

    private final VideoService service;

    public ServiceConnectCheckerRtsp(VideoService service) {
        this.service = service;
    }

    @Override
    public void onConnectionStartedRtsp(String rtspUrl) {
        service.onRtspConnectionStarted(rtspUrl);
    }

    @Override
    public void onConnectionSuccessRtsp() {
        service.onRtspConnectionSuccess();
    }

    @Override
    public void onConnectionFailedRtsp(String reason) {
        service.onRtspConnectionFailed(reason);
    }

    @Override
    public void onNewBitrateRtsp(long bitrate) {
        service.onRtspNewBitrate(bitrate);
    }

    @Override
    public void onDisconnectRtsp() {
        service.onRtspDisconnected();
    }

    @Override
    public void onAuthErrorRtsp() {
        service.onRtspAuthError();
    }

    @Override
    public void onAuthSuccessRtsp() {
        service.onRtspAuthSuccess();
    }
}


