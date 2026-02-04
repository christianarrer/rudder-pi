package biz.schrottplatz.rudderpi.util;

public final class NetUtil {

    private NetUtil() {} // kein new

    public static boolean isValidIPv4(String ip) {
        if (ip == null) return false;
        ip = ip.trim();
        String[] parts = ip.split("\\.", -1);
        if (parts.length != 4) return false;

        for (String p : parts) {
            if (p.isEmpty() || p.length() > 3) return false;
            for (int i = 0; i < p.length(); i++) {
                if (!Character.isDigit(p.charAt(i))) return false;
            }
            if (p.length() > 1 && p.startsWith("0")) return false;

            int v;
            try { v = Integer.parseInt(p); }
            catch (NumberFormatException e) { return false; }
            if (v < 0 || v > 255) return false;
        }
        return true;
    }

    public static boolean isValidTcpPort(String port) {
        if (port == null) return false;
        port = port.trim();

        if (port.isEmpty() || port.length() > 5) return false;

        for (int i = 0; i < port.length(); i++) {
            if (!Character.isDigit(port.charAt(i))) return false;
        }

        // optional, aber sauber: keine führenden Nullen wie "080"
        if (port.length() > 1 && port.startsWith("0")) return false;

        int v;
        try {
            v = Integer.parseInt(port);
        } catch (NumberFormatException e) {
            return false;
        }

        // TCP/UDP Ports: 1–65535
        if (v <= 0 || v > 65535) return false;

        return true;
    }

    public static boolean isValidTcpPort(int port) {
        return port > 0 && port <= 65535;
    }

}