// IconCatalog.mc — Maps icon IDs received over BLE to the text labels and
// status glyphs rendered by the CLI-aesthetic views.
//
// The icon registry is append-only and mirrors src/ohm/icons.py.
// Adding a new icon means appending a new constant and a new case to each
// switch below — never renumber or remove an existing entry.
//
// Two accessors:
//   • labelFor(iconId)              — short tool/intent name shown in
//                                     column 2 of the history view.
//   • statusGlyphFor(iconId, flags) — bracketed status glyph shown in
//                                     column 1 of the history view.

using Toybox.Graphics;
using Toybox.Lang;

module IconCatalog {
    const ICON_PLAY = 0x01;
    const ICON_PENCIL = 0x02;
    const ICON_EYE = 0x03;
    const ICON_GLOBE = 0x04;
    const ICON_CLIPBOARD = 0x05;
    const ICON_WRENCH = 0x06;
    const ICON_CHECK = 0x07;
    const ICON_GREEN_CIRCLE = 0x08;
    const ICON_PAUSE = 0x09;
    const ICON_STOP = 0x0a;
    const ICON_WARNING = 0x0b;
    const ICON_QUESTION = 0x0c;
    const ICON_NO_ENTRY = 0x0d;
    const ICON_STATUS_DOT = 0x0e;

    // Short tool/intent name shown in column 2 of the history view.
    // Mirrors the intent classification in history_encoder._classify.
    // Unknown icons return "" so the column stays clean.
    function labelFor(iconId) {
        switch (iconId) {
            case ICON_PLAY:
                return "run";
            case ICON_PENCIL:
                return "edit";
            case ICON_EYE:
                return "read";
            case ICON_GLOBE:
                return "web";
            case ICON_CLIPBOARD:
                return "todo";
            case ICON_WRENCH:
                return "tool";
            case ICON_CHECK:
                return "ok";
            case ICON_GREEN_CIRCLE:
                return "start";
            case ICON_PAUSE:
                return "idle";
            case ICON_STOP:
                return "stop";
            case ICON_WARNING:
                return "err";
            case ICON_QUESTION:
                return "ask";
            case ICON_NO_ENTRY:
                return "deny";
            case ICON_STATUS_DOT:
                return "info";
        }
        return "";
    }

    // Bracketed status glyph for column 1 of the history view.  Callers
    // should NOT call this when (flags & FLAG_SPINNER) is set — those rows
    // draw the animated bitmap spinner instead.
    //
    // The glyph is just the inner character; the caller wraps it in
    // brackets ("[" + g + "]") so accents and the bracket frame share a
    // single dc.drawText call.
    // Returns a 7-bit ASCII character only — Garmin system fonts have
    // patchy coverage of Unicode (the "✓" glyph in particular renders as
    // "?" on Fenix 7's default font), so we stick to printable ASCII.
    //
    // FLAG_ACCENT does NOT override the glyph — it only affects text color
    // (handled by the view) and statusColorFor().  This lets icons like
    // QUESTION ([?]) and NO_ENTRY ([x]) keep their identity while still
    // rendering in amber when accented.
    function statusGlyphFor(iconId, flags) {
        switch (iconId) {
            case ICON_CHECK:
                return "+";
            case ICON_GREEN_CIRCLE:
                return "+";
            case ICON_WARNING:
                return "!";
            case ICON_NO_ENTRY:
                return "x";
            case ICON_QUESTION:
                return "?";
            case ICON_PAUSE:
                return "~";
            case ICON_STOP:
                return "x";
            case ICON_STATUS_DOT:
                return ".";
        }
        // FLAG_ACCENT without a known icon falls back to "!"
        if (
            flags != null &&
            ((flags as Lang.Number) & HistoryDecoder.FLAG_ACCENT) != 0
        ) {
            return "!";
        }
        return "-";
    }

    // Color the column-1 status glyph should render in (CLI palette).
    // Mirrors the table in statusGlyphFor and accents from FLAG_ACCENT.
    function statusColorFor(iconId, flags) {
        if (
            flags != null &&
            ((flags as Lang.Number) & HistoryDecoder.FLAG_ACCENT) != 0
        ) {
            return Palette.active();
        }
        switch (iconId) {
            case ICON_CHECK:
                return Palette.done();
            case ICON_GREEN_CIRCLE:
                return Palette.done();
            case ICON_WARNING:
                return Palette.active();
            case ICON_NO_ENTRY:
                return Palette.active();
            case ICON_QUESTION:
                return Palette.active();
            case ICON_PAUSE:
                return Palette.chrome();
            case ICON_STOP:
                return Palette.chrome();
        }
        return Palette.text();
    }

    // Map an icon + flags to a pre-rendered glyph bitmap index.
    // Returns one of the StatusModel.GLYPH_* constants.  The view uses
    // this to draw `dc.drawBitmap` instead of `dc.drawText` so every
    // glyph is pixel-identical in size and alignment.
    function glyphBitmapIndex(iconId, flags) {
        switch (iconId) {
            case ICON_CHECK:
                return StatusModel.GLYPH_PLUS_GREEN;
            case ICON_GREEN_CIRCLE:
                return StatusModel.GLYPH_PLUS_GREEN;
            case ICON_WARNING:
                return StatusModel.GLYPH_BANG_AMBER;
            case ICON_NO_ENTRY:
                return StatusModel.GLYPH_X_AMBER;
            case ICON_QUESTION:
                return StatusModel.GLYPH_QUESTION_AMBER;
            case ICON_PAUSE:
                return StatusModel.GLYPH_TILDE_CHROME;
            case ICON_STOP:
                return StatusModel.GLYPH_X_CHROME;
            case ICON_STATUS_DOT:
                return StatusModel.GLYPH_DOT_WHITE;
        }
        // FLAG_ACCENT without a known icon falls back to bang amber
        if (
            flags != null &&
            ((flags as Lang.Number) & HistoryDecoder.FLAG_ACCENT) != 0
        ) {
            return StatusModel.GLYPH_BANG_AMBER;
        }
        return StatusModel.GLYPH_MINUS_WHITE;
    }
}
