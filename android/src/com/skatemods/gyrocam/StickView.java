package com.skatemods.gyrocam;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.MotionEvent;
import android.view.View;

/** Thumb stick that springs back to centre. Vertical-only mode is used for the lift slider. */
public class StickView extends View {
    private final Paint base = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint knob = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final boolean verticalOnly;
    private final String label;
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);

    /** Normalised output, -1..1. y is positive up. */
    public volatile float valueX, valueY;

    public StickView(Context context, boolean verticalOnly, String label) {
        super(context);
        this.verticalOnly = verticalOnly;
        this.label = label;
        base.setColor(0x33FFFFFF);
        knob.setColor(0xCCFFB000);
        text.setColor(0x99FFFFFF);
        text.setTextAlign(Paint.Align.CENTER);
    }

    private float radius() {
        return verticalOnly ? getHeight() / 2f - getWidth() / 2f : Math.min(getWidth(), getHeight()) / 2f * 0.8f;
    }

    @Override
    protected void onDraw(Canvas c) {
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        float knobR = verticalOnly ? getWidth() * 0.4f : radius() * 0.35f;
        if (verticalOnly) {
            c.drawRoundRect(cx - getWidth() * 0.3f, 0, cx + getWidth() * 0.3f, getHeight(),
                    getWidth() * 0.3f, getWidth() * 0.3f, base);
        } else {
            c.drawCircle(cx, cy, radius(), base);
        }
        c.drawCircle(cx + valueX * radius(), cy - valueY * radius(), knobR, knob);
        text.setTextSize(getWidth() * (verticalOnly ? 0.22f : 0.08f));
        c.drawText(label, cx, getHeight() - text.getTextSize() * 0.3f, text);
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        int a = e.getActionMasked();
        if (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) {
            valueX = valueY = 0;
        } else {
            float r = radius();
            float dx = verticalOnly ? 0 : (e.getX() - getWidth() / 2f) / r;
            float dy = -(e.getY() - getHeight() / 2f) / r;
            float len = (float) Math.sqrt(dx * dx + dy * dy);
            if (len > 1) { dx /= len; dy /= len; }
            valueX = dx;
            valueY = dy;
        }
        invalidate();
        return true;
    }
}
