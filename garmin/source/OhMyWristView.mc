// OhMyWristView.mc — History view, CLI / terminal aesthetic.
//
// Layout zones (round watch face, percentages of width × height):
//
//   [ admin@wrist:~$ ]            ← header prompt, FONT_XTINY, chrome gray
//     [+] ok: done                ← status + "intent: label"
//      ✦  edit: ClaudeGar…iew.mc  ← active row: amber bitmap spinner
//     [-] run: pytest
//     sys.status: ok · 35s ago    ← footer, chrome gray
//
// Fixed at 3 body rows so the layout is stable as events arrive.  The
// status column is a bracketed ASCII glyph (or the spinner bitmap
// for FLAG_SPINNER rows) and the rest of the line is a single text run
// "intent: label" middle-truncated to the available width.
//
// The view never inspects icon IDs directly — it asks IconCatalog for
// the glyph, color, and label.  Adding a new icon means appending to
// IconCatalog only.

using Toybox.Graphics;
using Toybox.Lang;
using Toybox.System;
using Toybox.WatchUi;

class OhMyWristView extends WatchUi.View {
    function initialize() {
        View.initialize();
    }

    function onLayout(dc) {
        // No XML layout — everything is drawn programmatically.
    }

    function onUpdate(dc) {
        var w = dc.getWidth();
        var h = dc.getHeight();

        // Background — true black per CLI palette.
        dc.setColor(Palette.bg(), Palette.bg());
        dc.clear();

        // Screen shape for chord-width calculations.
        var shape = System.getDeviceSettings().screenShape;
        var chromeFont = Graphics.FONT_XTINY;

        // Header prompt — chrome gray, centered.
        // Adaptive Y: start at 10%, step inward up to 20% if text clips.
        var headerText = WatchUi.loadResource(Rez.Strings.HeaderPrompt);
        var headerTextW = dc.getTextWidthInPixels(headerText, chromeFont);
        var stepPx = (h * 0.02).toNumber();
        if (stepPx < 1) {
            stepPx = 1;
        }
        var headerY = TextUtil.findFitY(
            (h * 0.1).toNumber(), // preferred Y
            (h * 0.2).toNumber(), // limit Y (don't crowd body)
            stepPx,
            headerTextW,
            w,
            h,
            shape,
            0.9 // 90% of chord usable (leave edge padding)
        );

        dc.setColor(Palette.chrome(), Graphics.COLOR_TRANSPARENT);
        // If text STILL doesn't fit at the limit Y, truncate it.
        var headerAvail = (
            TextUtil.getChordWidth(w, h, headerY, shape) * 0.9
        ).toNumber();
        var headerDraw =
            headerTextW <= headerAvail
                ? headerText
                : TextUtil.fitMiddleTruncate(
                      dc,
                      headerText,
                      chromeFont,
                      headerAvail
                  );
        dc.drawText(
            w / 2,
            headerY,
            chromeFont,
            headerDraw,
            Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER
        );

        // Body — history stack or empty-state placeholder.
        var entries = StatusModel.getEntries();
        if (entries.size() == 0) {
            var placeholder = StatusModel.getIsConnected()
                ? "// idle"
                : "// connecting...";
            dc.setColor(Palette.chrome(), Graphics.COLOR_TRANSPARENT);
            dc.drawText(
                w / 2,
                h * 0.5,
                Graphics.FONT_SMALL,
                placeholder,
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER
            );
        } else {
            _drawStack(dc, w, h, entries);
        }

        // Footer — combined status + elapsed string, chrome gray.
        // Adaptive Y: start at 90%, step inward up to 80% if text clips.
        var footerText = StatusModel.getFooterText();
        var footerTextW = dc.getTextWidthInPixels(footerText, chromeFont);
        var footerY = TextUtil.findFitY(
            (h * 0.9).toNumber(), // preferred Y
            (h * 0.8).toNumber(), // limit Y (don't crowd body)
            stepPx,
            footerTextW,
            w,
            h,
            shape,
            0.9
        );

        dc.setColor(Palette.chrome(), Graphics.COLOR_TRANSPARENT);
        // If text STILL doesn't fit at the limit Y, truncate it.
        var footerAvail = (
            TextUtil.getChordWidth(w, h, footerY, shape) * 0.9
        ).toNumber();
        var footerDraw =
            footerTextW <= footerAvail
                ? footerText
                : TextUtil.fitMiddleTruncate(
                      dc,
                      footerText,
                      chromeFont,
                      footerAvail
                  );
        dc.drawText(
            w / 2,
            footerY,
            chromeFont,
            footerDraw,
            Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER
        );
    }

    // Number of body rows.  Fixed at 3 (the design used to expand to 7
    // when the deque grew, but it made the layout jitter as events
    // arrived).
    const BODY_ROWS = 3;

    // All glyph bitmaps (static + spinner) share the same pixel
    // dimensions (set by tools/build_spinner_frames.py).  Update these
    // constants if the build script changes the canvas size.
    const GLYPH_BMP_W = 50;
    const GLYPH_BMP_H = 22;

    // Render up to BODY_ROWS entries newest-at-top, centered vertically.
    //
    // Column 1 is a pre-rendered bitmap (spinner or static glyph) —
    // every bitmap is exactly GLYPH_BMP_W × GLYPH_BMP_H so alignment
    // is pixel-perfect regardless of proportional font metrics.
    //
    // Column 2 is the "intent: label" text run drawn with dc.drawText.
    //
    // Rows dim progressively: newest (i=0) at 100 %, next at 90 %, then
    // 80 %.  Dimming applies to the text column only; the glyph bitmap
    // stays at full brightness for legibility.
    function _drawStack(dc, w, h, entries as Lang.Array<Lang.Dictionary>) {
        var n = entries.size();

        var font = Graphics.FONT_SMALL;
        var lineH = dc.getFontHeight(font) + 2;
        if (lineH <= 0) {
            lineH = 24;
        }

        var visible = n < BODY_ROWS ? n : BODY_ROWS;

        // Center the row block on the screen midline.
        var blockHeight = visible * lineH;
        var blockTop = ((h - blockHeight) / 2).toNumber();

        // Column positions — glyph left-aligned at the original left
        // margin; text starts right after the bitmap with a small gap.
        var glyphX = (w * 0.06).toNumber();
        var textX = glyphX + GLYPH_BMP_W + 4;
        var textRightEdge = (w * 0.95).toNumber();
        var textWidthPx = textRightEdge - textX;
        if (textWidthPx < 1) {
            textWidthPx = 1;
        }

        for (var i = 0; i < visible; i++) {
            var entry = entries[n - 1 - i]; // newest at i = 0
            var y = blockTop + i * lineH;

            // Progressive dimming: 100 % → 90 % → 80 %.
            var dimPct = 100 - i * 10;

            var flagsRaw = entry.get(:flags) as Lang.Number?;
            var flags = flagsRaw != null ? flagsRaw as Lang.Number : 0;
            var icon = entry.get(:icon);

            // Column 1's glyph follows :statusIcon when set (a
            // completion event collapsed into this row), otherwise the
            // original :icon.  Column 2 always uses :icon for the
            // intent name so "edit: DESIGN.md" survives the collapse.
            var statusIcon = entry.get(:statusIcon);
            if (statusIcon == null) {
                statusIcon = icon;
            }

            var isActive = (flags & HistoryDecoder.FLAG_SPINNER) != 0;
            var by = y + (lineH - GLYPH_BMP_H) / 2;

            // Column 1 — bitmap glyph (spinner or static).
            if (isActive) {
                var bmp = StatusModel.currentSpinnerBitmap();
                if (bmp != null) {
                    dc.drawBitmap(glyphX, by, bmp);
                } else {
                    // Fallback if spinner bitmaps failed to load.
                    dc.setColor(Palette.active(), Graphics.COLOR_TRANSPARENT);
                    dc.drawText(
                        glyphX,
                        y,
                        font,
                        "[*]",
                        Graphics.TEXT_JUSTIFY_LEFT
                    );
                }
            } else {
                var idx = IconCatalog.glyphBitmapIndex(statusIcon, flags);
                var bmp = StatusModel.glyphBitmap(idx);
                if (bmp != null) {
                    dc.drawBitmap(glyphX, by, bmp);
                } else {
                    // Fallback if glyph bitmaps failed to load.
                    dc.setColor(
                        IconCatalog.statusColorFor(statusIcon, flags),
                        Graphics.COLOR_TRANSPARENT
                    );
                    dc.drawText(
                        glyphX,
                        y,
                        font,
                        "[" +
                            IconCatalog.statusGlyphFor(statusIcon, flags) +
                            "]",
                        Graphics.TEXT_JUSTIFY_LEFT
                    );
                }
            }

            // Column 2 — "intent: label" text run (left-aligned).
            //
            // The newest row (top line) gets a blinking block cursor
            // appended after the text.  We reserve space for the cursor
            // when truncating so it never overflows the available width.
            var line = _formatLine(icon, entry.get(:text) as Lang.String?);
            if (line.length() > 0) {
                var color;
                if (isActive) {
                    color = Palette.active();
                } else if ((flags & HistoryDecoder.FLAG_ACCENT) != 0) {
                    color = Palette.active();
                } else if ((flags & HistoryDecoder.FLAG_DIM) != 0) {
                    color = Palette.chrome();
                } else {
                    color = Palette.text();
                }
                dc.setColor(
                    Palette.dim(color, dimPct),
                    Graphics.COLOR_TRANSPARENT
                );

                // Cursor metrics: a slim block (30 % of font height wide,
                // 70 % tall) with a 2 px gap — light enough not to
                // overpower the text but clearly visible as a cursor.
                var fontH = dc.getFontHeight(font);
                var curW = (fontH * 0.3).toNumber();
                if (curW < 3) {
                    curW = 3;
                }
                var curH = (fontH * 0.7).toNumber();
                if (curH < 6) {
                    curH = 6;
                }
                var curGap = 2;
                var showCursor = i == 0;
                var cursorReserve = showCursor ? curGap + curW : 0;

                var truncated = TextUtil.fitMiddleTruncate(
                    dc,
                    line,
                    font,
                    textWidthPx - cursorReserve
                );
                dc.drawText(
                    textX,
                    y,
                    font,
                    truncated,
                    Graphics.TEXT_JUSTIFY_LEFT
                );

                // Draw blinking slim cursor on the newest row only.
                if (showCursor && StatusModel.cursorVisible) {
                    var textW = dc.getTextWidthInPixels(truncated, font);
                    var cx = textX + textW + curGap;
                    var cy = y + (lineH - curH) / 2;
                    dc.fillRectangle(cx, cy, curW, curH);
                }
            }
        }
    }

    // Compose the body line.  Examples:
    //   icon=PENCIL, text="DESIGN.md"  → "edit: DESIGN.md"
    //   icon=CHECK,  text=""           → "ok"
    //   icon=PLAY,   text="pytest"     → "run: pytest"
    function _formatLine(icon, text) {
        var intent = IconCatalog.labelFor(icon);
        var label = text != null ? text as Lang.String : "";
        if (intent.length() > 0 && label.length() > 0) {
            return intent + ": " + label;
        }
        if (intent.length() > 0) {
            return intent;
        }
        return label;
    }

}
