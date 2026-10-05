package com.skatemods.gyrocam;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * Streams the phone's orientation to the GyroCam PC bridge over UDP.
 * Packet layout is documented in README.md and must match gyrocam_bridge.py.
 */
public class MainActivity extends Activity
        implements SensorEventListener, SeekBar.OnSeekBarChangeListener {
    private static final int PORT = 47823;
    private static final int DISCOVERY_PORT = 47824;
    private static final int SEND_INTERVAL_MS = 8; // ~120 Hz

    private SensorManager sensors;
    private Sensor rotationSensor;
    private WifiManager.MulticastLock multicastLock;

    // Shared between UI, sensor and network threads.
    private volatile float qx, qy, qz, qw = 1;
    private volatile int displayRotation;
    private volatile boolean trackingToggle, trackHeld, boostHeld, playHeld;
    private volatile float zoomValue, sens = 1f, fovTarget = 70f;
    private volatile int fovSyncCount;
    private static final int FOV_MIN = 50, FOV_MAX = 120, FOV_STEP = 5;
    private volatile int recenterCount, homeCount;
    private volatile String manualHost = "";
    private volatile InetAddress discoveredHost;
    private volatile long lastAckAt, lastSendError;
    private volatile int ackFlags, ackMode, ackFov;
    private volatile boolean running;

    private volatile StickView moveStick, liftStick, lookStick;
    private TextView sensLabel, fovLabel;
    private TextView status;
    private Button trackButton;
    private CheckBox holdMode;
    private EditText hostField;
    private DatagramSocket socket;
    private Thread sender, receiver, discovery;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        sensors = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        rotationSensor = sensors.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR);
        if (rotationSensor == null) rotationSensor = sensors.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR);
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wifi != null) {
            multicastLock = wifi.createMulticastLock("gyrocam-discovery");
            multicastLock.setReferenceCounted(false);
        }
        buildUi();
    }

    private void buildUi() {
        // Keep what the user typed/checked across rotations.
        SharedPreferences prefs = getPreferences(MODE_PRIVATE);
        String host = hostField != null ? hostField.getText().toString() : prefs.getString("host", "");
        boolean hold = holdMode != null ? holdMode.isChecked() : prefs.getBoolean("hold", false);
        if (hostField == null) {
            sens = prefs.getFloat("sens", 1f);
            fovTarget = prefs.getFloat("fov", 70f);
        }
        boolean portrait = getResources().getConfiguration().orientation == Configuration.ORIENTATION_PORTRAIT;
        int pad = dp(10);

        TextView title = new TextView(this);
        title.setText("GyroCam");
        title.setTextColor(0xFFFFB000);
        title.setTextSize(20);
        title.setTypeface(Typeface.DEFAULT_BOLD);

        status = new TextView(this);
        status.setTextColor(Color.LTGRAY);
        status.setTextSize(12);
        status.setGravity(Gravity.CENTER);

        hostField = new EditText(this);
        hostField.setHint("PC IP (blank = auto)");
        hostField.setHintTextColor(Color.GRAY);
        hostField.setTextColor(Color.WHITE);
        hostField.setSingleLine(true);
        hostField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        hostField.setGravity(Gravity.CENTER);
        hostField.setText(host);
        manualHost = host.trim();

        holdMode = new CheckBox(this);
        holdMode.setChecked(hold);
        holdMode.setText("Hold to track");
        holdMode.setTextColor(Color.LTGRAY);
        holdMode.setOnClickListener(v -> {
            trackingToggle = trackHeld = false;
            refreshTrackButton();
        });

        trackButton = new Button(this);
        trackButton.setTextSize(20);
        trackButton.setOnTouchListener((v, e) -> {
            int a = e.getActionMasked();
            if (holdMode.isChecked()) {
                if (a == MotionEvent.ACTION_DOWN) trackHeld = true;
                if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) trackHeld = false;
            } else if (a == MotionEvent.ACTION_DOWN) {
                trackingToggle = !trackingToggle;
            }
            refreshTrackButton();
            return false;
        });

        Button recenter = new Button(this);
        recenter.setText("RECENTER\n(hold = set home)");
        recenter.setTextSize(12);
        recenter.setOnClickListener(v -> recenterCount++);
        recenter.setOnLongClickListener(v -> {
            homeCount++;
            Toast.makeText(this, "Home view set to where the camera is now", Toast.LENGTH_SHORT).show();
            return true;
        });
        Button play = holdButton("\u25B6 PLAY (A)", d -> playHeld = d);
        play.setTextColor(0xFF7CFC7C);

        // Sensitivity: phone degrees -> camera degrees.
        sensLabel = new TextView(this);
        sensLabel.setTextColor(Color.WHITE);
        sensLabel.setTextSize(15);
        sensLabel.setGravity(Gravity.CENTER);
        Button sensDown = new Button(this);
        sensDown.setText("SENS \u2212");
        sensDown.setOnClickListener(v -> changeSens(1 / 1.25f));
        Button sensUp = new Button(this);
        sensUp.setText("SENS +");
        sensUp.setOnClickListener(v -> changeSens(1.25f));
        changeSens(1f);

        // FOV slider: absolute target, the bridge taps the D-pad until the game matches.
        fovLabel = new TextView(this);
        fovLabel.setTextColor(Color.WHITE);
        fovLabel.setTextSize(14);
        fovLabel.setGravity(Gravity.CENTER);
        SeekBar fovBar = new SeekBar(this);
        fovBar.setMax((FOV_MAX - FOV_MIN) / FOV_STEP);
        fovBar.setProgress(Math.round((fovTarget - FOV_MIN) / FOV_STEP));
        fovBar.setOnSeekBarChangeListener(this);
        fovLabel.setText("FOV " + (int) fovTarget + "\u00b0");
        Button fovSync = new Button(this);
        fovSync.setText("SYNC");
        fovSync.setOnClickListener(v -> {
            fovSyncCount++;
            Toast.makeText(this, "Re-syncing FOV: stepping to the minimum, then back to " + (int) fovTarget + "\u00b0",
                    Toast.LENGTH_SHORT).show();
        });
        LinearLayout fovRow = new LinearLayout(this);
        fovRow.setOrientation(LinearLayout.HORIZONTAL);
        fovRow.setGravity(Gravity.CENTER_VERTICAL);
        fovRow.addView(fovLabel, new LinearLayout.LayoutParams(dp(78), -2));
        fovRow.addView(fovBar, new LinearLayout.LayoutParams(0, dp(44), 1f));
        fovRow.addView(fovSync, new LinearLayout.LayoutParams(-2, -2));
        Button boost = holdButton("BOOST", d -> boostHeld = d);

        moveStick = new StickView(this, false, "MOVE");
        lookStick = new StickView(this, false, "LOOK");
        liftStick = new StickView(this, true, "UP/DN");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF111114);
        root.setPadding(pad, pad, pad, pad);

        if (portrait) {
            root.addView(title);
            root.addView(status, new LinearLayout.LayoutParams(-1, -2));
            root.addView(hostField, new LinearLayout.LayoutParams(-1, -2));
            root.addView(trackButton, new LinearLayout.LayoutParams(-1, 0, 0.55f));
            root.addView(row(recenter, play), new LinearLayout.LayoutParams(-1, -2));
            root.addView(row(sensDown, sensLabel, sensUp, boost), new LinearLayout.LayoutParams(-1, -2));
            root.addView(fovRow, new LinearLayout.LayoutParams(-1, -2));
            LinearLayout sticks = new LinearLayout(this);
            sticks.setOrientation(LinearLayout.HORIZONTAL);
            sticks.addView(moveStick, new LinearLayout.LayoutParams(0, -1, 1f));
            sticks.addView(liftStick, new LinearLayout.LayoutParams(dp(56), -1));
            sticks.addView(lookStick, new LinearLayout.LayoutParams(0, -1, 1f));
            root.addView(sticks, new LinearLayout.LayoutParams(-1, 0, 1f));
            root.addView(holdMode);
        } else {
            root.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout left = new LinearLayout(this);
            left.setOrientation(LinearLayout.HORIZONTAL);
            left.addView(moveStick, new LinearLayout.LayoutParams(0, -1, 1f));
            left.addView(liftStick, new LinearLayout.LayoutParams(dp(56), -1));
            root.addView(left, new LinearLayout.LayoutParams(0, -1, 1.1f));

            LinearLayout center = new LinearLayout(this);
            center.setOrientation(LinearLayout.VERTICAL);
            center.setGravity(Gravity.CENTER_HORIZONTAL);
            center.setPadding(pad, 0, pad, 0);
            LinearLayout top = row(title, holdMode);
            center.addView(top, new LinearLayout.LayoutParams(-1, -2));
            center.addView(status, new LinearLayout.LayoutParams(-1, -2));
            center.addView(hostField, new LinearLayout.LayoutParams(-1, -2));
            center.addView(trackButton, new LinearLayout.LayoutParams(-1, 0, 1f));
            center.addView(row(recenter, play), new LinearLayout.LayoutParams(-1, -2));
            center.addView(row(sensDown, sensLabel, sensUp, boost), new LinearLayout.LayoutParams(-1, -2));
            center.addView(fovRow, new LinearLayout.LayoutParams(-1, -2));
            root.addView(center, new LinearLayout.LayoutParams(0, -1, 1.5f));

            root.addView(lookStick, new LinearLayout.LayoutParams(0, -1, 1f));
        }

        setContentView(root);
        refreshTrackButton();
    }

    // FOV slider. (Anonymous classes trip a d8 bug in build-tools 34, so the activity listens.)
    @Override
    public void onProgressChanged(SeekBar b, int progress, boolean fromUser) {
        fovTarget = FOV_MIN + progress * FOV_STEP;
        fovLabel.setText("FOV " + (int) fovTarget + "°");
    }

    @Override
    public void onStartTrackingTouch(SeekBar b) {}

    @Override
    public void onStopTrackingTouch(SeekBar b) {}

    private interface Held {
        void set(boolean down);
    }

    /** A button that reports press/release instead of clicks. */
    private Button holdButton(String label, Held held) {
        Button b = new Button(this);
        b.setText(label);
        b.setOnTouchListener((v, e) -> {
            int a = e.getActionMasked();
            if (a == MotionEvent.ACTION_DOWN) held.set(true);
            if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) held.set(false);
            return false;
        });
        return b;
    }

    private LinearLayout row(View... views) {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        for (View v : views) r.addView(v, new LinearLayout.LayoutParams(0, -2, 1f));
        return r;
    }

    private void changeSens(float factor) {
        sens = Math.max(0.25f, Math.min(4f, sens * factor));
        if (Math.abs(sens - 1f) < 0.02f) sens = 1f;
        sensLabel.setText(String.format(java.util.Locale.US, "%.2f\u00d7", sens));
    }

    private void refreshTrackButton() {
        boolean on = isTracking();
        trackButton.setText(on ? "TRACKING" : (holdMode.isChecked() ? "HOLD TO TRACK" : "START TRACKING"));
        trackButton.setBackgroundColor(on ? 0xFFFFB000 : 0xFF2A2A30);
        trackButton.setTextColor(on ? Color.BLACK : Color.WHITE);
    }

    private boolean isTracking() {
        return trackingToggle || trackHeld;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_UP) {
            if (event.getRepeatCount() == 0) recenterCount++;
            return true;
        }
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) {
            if (event.getRepeatCount() == 0) {
                trackingToggle = !trackingToggle;
                refreshTrackButton();
            }
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        displayRotation = getWindowManager().getDefaultDisplay().getRotation();
        status.removeCallbacks(statusUpdater);
        buildUi();
        status.post(statusUpdater);
    }

    @Override
    protected void onResume() {
        super.onResume();
        displayRotation = getWindowManager().getDefaultDisplay().getRotation();
        if (rotationSensor != null) {
            sensors.registerListener(this, rotationSensor, SensorManager.SENSOR_DELAY_FASTEST);
        }
        if (multicastLock != null) multicastLock.acquire();
        startNetwork();
        status.post(statusUpdater);
    }

    @Override
    protected void onPause() {
        super.onPause();
        sensors.unregisterListener(this);
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
        trackingToggle = trackHeld = false;
        stopNetwork();
        status.removeCallbacks(statusUpdater);
        getPreferences(MODE_PRIVATE).edit()
                .putString("host", hostField.getText().toString().trim())
                .putBoolean("hold", holdMode.isChecked())
                .putFloat("sens", sens)
                .putFloat("fov", fovTarget)
                .apply();
    }

    private final float[] quat = new float[4];

    @Override
    public void onSensorChanged(SensorEvent e) {
        SensorManager.getQuaternionFromVector(quat, e.values); // w, x, y, z
        qw = quat[0];
        qx = quat[1];
        qy = quat[2];
        qz = quat[3];
    }

    @Override
    public void onAccuracyChanged(Sensor s, int accuracy) {}

    private final Runnable statusUpdater = this::updateStatus;

    private void updateStatus() {
        manualHost = hostField.getText().toString().trim();
        String text;
        InetAddress target = discoveredHost;
        if (rotationSensor == null) {
            text = "This phone has no rotation sensor.";
        } else if (SystemClock.elapsedRealtime() - lastAckAt < 1500) {
            String host = manualHost.isEmpty() && target != null ? target.getHostAddress() : manualHost;
            text = "Connected to " + host + " · " + (ackMode == 0 ? "mouse" : "gamepad") + " mode\n"
                    + ((ackFlags & 1) != 0 ? "Game focused" : "Game not focused — click into skate.")
                    + (ackFov > 0 ? " · game FOV " + ackFov + "°" : "");
        } else if (manualHost.isEmpty() && target == null) {
            text = "Searching for the PC bridge…\nStart gyrocam_bridge on your PC (same Wi-Fi).";
        } else {
            text = "No reply from " + (manualHost.isEmpty() ? target.getHostAddress() : manualHost)
                    + "\nCheck the IP and allow UDP " + PORT + " in Windows Firewall.";
        }
        status.setText(text);
        status.postDelayed(statusUpdater, 250);
    }

    private void startNetwork() {
        running = true;
        try {
            socket = new DatagramSocket();
            socket.setSoTimeout(500);
        } catch (Exception e) {
            status.setText("Socket error: " + e.getMessage());
            return;
        }
        sender = new Thread(this::sendLoop, "gyrocam-send");
        receiver = new Thread(this::receiveLoop, "gyrocam-recv");
        discovery = new Thread(this::discoveryLoop, "gyrocam-discovery");
        sender.start();
        receiver.start();
        discovery.start();
    }

    private void stopNetwork() {
        running = false;
        if (socket != null) socket.close();
        for (Thread t : new Thread[]{sender, receiver, discovery}) {
            if (t != null) t.interrupt();
        }
    }

    private InetAddress resolveTarget(String cachedName, InetAddress cached) {
        String host = manualHost;
        if (host.isEmpty()) return discoveredHost;
        if (host.equals(cachedName)) return cached;
        try {
            return InetAddress.getByName(host);
        } catch (Exception e) {
            return null;
        }
    }

    private void sendLoop() {
        ByteBuffer buf = ByteBuffer.allocate(64).order(ByteOrder.LITTLE_ENDIAN);
        DatagramPacket packet = new DatagramPacket(buf.array(), 64);
        short seq = 0;
        String cachedName = null;
        InetAddress cached = null;
        while (running) {
            InetAddress target = resolveTarget(cachedName, cached);
            if (!manualHost.isEmpty()) {
                cachedName = manualHost;
                cached = target;
            }
            if (target != null) {
                buf.clear();
                buf.put((byte) 'G').put((byte) 'Y').put((byte) 'C').put((byte) '1');
                buf.putShort(seq++);
                int flags = (isTracking() ? 1 : 0) | (boostHeld ? 2 : 0) | (playHeld ? 4 : 0);
                buf.put((byte) flags);
                buf.put((byte) recenterCount);
                buf.put((byte) displayRotation);
                buf.put((byte) homeCount).put((byte) fovSyncCount).put((byte) 0);
                buf.putFloat(qx).putFloat(qy).putFloat(qz).putFloat(qw);
                buf.putFloat(moveStick.valueX).putFloat(moveStick.valueY).putFloat(liftStick.valueY);
                buf.putInt((int) SystemClock.elapsedRealtime());
                buf.putFloat(zoomValue).putFloat(sens);
                buf.putFloat(lookStick.valueX).putFloat(lookStick.valueY);
                buf.putFloat(fovTarget);
                packet.setSocketAddress(new InetSocketAddress(target, PORT));
                try {
                    socket.send(packet);
                } catch (Exception e) {
                    lastSendError = SystemClock.elapsedRealtime();
                }
            }
            SystemClock.sleep(SEND_INTERVAL_MS);
        }
    }

    private void receiveLoop() {
        byte[] data = new byte[64];
        DatagramPacket p = new DatagramPacket(data, data.length);
        while (running) {
            try {
                socket.receive(p);
                if (p.getLength() >= 8 && data[0] == 'G' && data[1] == 'Y' && data[2] == 'C' && data[3] == 'A') {
                    ackFlags = data[4];
                    ackMode = data[5];
                    ackFov = p.getLength() >= 9 ? data[8] & 0xFF : 0;
                    lastAckAt = SystemClock.elapsedRealtime();
                }
            } catch (SocketTimeoutException ignored) {
            } catch (Exception e) {
                if (!running) return;
            }
        }
    }

    private void discoveryLoop() {
        DatagramSocket ds = null;
        try {
            ds = new DatagramSocket(null);
            ds.setReuseAddress(true);
            ds.setBroadcast(true);
            ds.bind(new InetSocketAddress(DISCOVERY_PORT));
            ds.setSoTimeout(500);
            byte[] data = new byte[64];
            DatagramPacket p = new DatagramPacket(data, data.length);
            while (running) {
                try {
                    ds.receive(p);
                    if (p.getLength() >= 6 && data[0] == 'G' && data[1] == 'Y' && data[2] == 'C' && data[3] == 'B') {
                        discoveredHost = p.getAddress();
                    }
                } catch (SocketTimeoutException ignored) {
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (ds != null) ds.close();
        }
    }
}
