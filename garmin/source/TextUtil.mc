// TextUtil.mc — Small string helpers used by the CLI-styled views.
//
// Operates on Monkey C String char indices.  The daemon already produces
// byte-safe UTF-8 (≤ 18 bytes via _utf8_truncate in history_encoder.py), so
// further byte-level care is not required here — char-count truncation is
// sufficient.

using Toybox.Lang;
using Toybox.Math;
using Toybox.System;

module TextUtil {
    function extractSignedNumber(str, key, fallback) {
        var idx = str.find(key);
        if (idx == null) {
            return fallback;
        }
        idx += key.length();
        var end = idx;
        while (end < str.length()) {
            var ch = str.substring(end, end + 1);
            if (ch.equals(",") || ch.equals("}")) {
                break;
            }
            end++;
        }
        var token = str.substring(idx, end);
        if (token == null || token.length() == 0) {
            return fallback;
        }
        var n = token.toNumber();
        return n == null ? fallback : n;
    }

    // ─── Round-screen geometry ─────────────────────────────────────────
    //
    // On a round display the usable horizontal width at any Y position is
    // the chord of the circle at that height.  For a circle of diameter d
    // centered in a d×d bounding box:
    //
    //   chordWidth(y) = 2 · √(r² − (y − r)²)   where r = d / 2
    //
    // On rectangular / semi-round / semi-octagon screens we just return
    // the full width — text will never be clipped horizontally.

    // Returns the available horizontal pixel width at the given Y
    // position, accounting for round-screen geometry.
    //
    //   w, h  — dc.getWidth(), dc.getHeight()
    //   y     — vertical pixel position (0 = top of screen)
    //   shape — System.getDeviceSettings().screenShape
    //
    function getChordWidth(w, h, y, shape) {
        if (shape != System.SCREEN_SHAPE_ROUND) {
            return w;
        }
        // For a round screen the bounding box is square (w == h), so
        // radius = w / 2.  Compute distance from center.
        var r = w / 2.0;
        var dy = y - r;
        if (dy < 0) {
            dy = -dy;
        } // abs
        if (dy >= r) {
            return 0;
        }
        return (2.0 * Math.sqrt(r * r - dy * dy)).toNumber();
    }

    // Find the best Y position for centered text on a round screen.
    //
    // Starts at `preferredY` and steps toward the screen center by
    // `stepPx` pixels until the chord is wide enough for `textWidthPx`,
    // or the limit is reached.  Returns the resolved Y position.
    //
    //   preferredY  — ideal Y in pixels (e.g. h*0.10 for header)
    //   limitY      — how far inward we're willing to move (e.g. h*0.20)
    //   stepPx      — increment per step in pixels (e.g. h*0.02)
    //   textWidthPx — measured width of the text to fit
    //   w, h        — screen dimensions
    //   shape       — screenShape from DeviceSettings
    //   margin      — fraction of chord to use (0.90 = leave 10% padding)
    //
    function findFitY(
        preferredY,
        limitY,
        stepPx,
        textWidthPx,
        w,
        h,
        shape,
        margin
    ) {
        var y = preferredY;
        var movingDown = limitY > preferredY; // header moves down, footer moves up

        while (true) {
            var chord = getChordWidth(w, h, y, shape);
            var avail = (chord * margin).toNumber();
            if (textWidthPx <= avail) {
                return y; // text fits at this Y
            }
            // Step toward center
            if (movingDown) {
                y = y + stepPx;
                if (y >= limitY) {
                    return limitY;
                }
            } else {
                y = y - stepPx;
                if (y <= limitY) {
                    return limitY;
                }
            }
        }
        return limitY; // unreachable, but satisfies compiler
    }

    const ELLIPSIS = "…";

    // Pixel-measured middle-truncate.  Returns the longest middle-
    // truncated form of `str` whose rendered width in `font` fits within
    // `maxPx`.  Caller passes the live `dc` so we can use the actual
    // proportional-font metrics instead of estimating with an "M".
    //
    //   fitMiddleTruncate(dc, "OhMyWristView.mc", FONT_SMALL, 140) →
    //     "ClaudeGarm…ew.mc"   (or whichever length fills the budget)
    //
    // Falls back to `str` unchanged when it already fits, and to the
    // bare ellipsis when even one character + ellipsis exceeds maxPx.
    function fitMiddleTruncate(dc, str, font, maxPx) {
        if (str == null) {
            return "";
        }
        var s = str as Lang.String;
        if (dc.getTextWidthInPixels(s, font) <= maxPx) {
            return s;
        }
        var n = s.length();
        if (n <= 2) {
            return ELLIPSIS;
        }

        // Shrink one char at a time from the longest candidate down.
        // O(n) measurements; cheap for typical 18-char labels.
        for (var keep = n - 1; keep >= 2; keep--) {
            var head = (keep + 1) / 2;
            var tail = keep - head;
            var candidate =
                s.substring(0, head) + ELLIPSIS + s.substring(n - tail, n);
            if (dc.getTextWidthInPixels(candidate, font) <= maxPx) {
                return candidate;
            }
        }
        return ELLIPSIS;
    }
}
