// StatsModel.mc — Per-provider session statistics
//
// Each provider (Claude / OpenCode) has its own StatsData instance so that
// concurrent sessions don't clobber each other on screen. The desktop daemon
// pushes a separate compact JSON payload per provider over its own
// characteristic; BleManager routes each notification to the matching
// instance below.
//
// Payload format (all integers, single-character keys):
//   {"d":312,"t":47,"e":9,"b":23,"i":45,"s":120,"p":1}
//
//   d  duration in seconds
//   t  total tool calls
//   e  unique files edited
//   b  bash commands run
//   i  idle seconds
//   s  seconds since last completion (-1 = never)
//   p  last provider id (0 = none, 1 = claude, 2 = opencode)

using Toybox.Lang;

class StatsData {
    var duration = 0;
    var toolCalls = 0;
    var filesEdited = 0;
    var bashCount = 0;
    var idleSeconds = 0;
    var lastCompletion = "never";
    var provider = "";

    function initialize() {}

    // -----------------------------------------------------------------------
    // parsePayload — update all fields from a compact JSON string
    // -----------------------------------------------------------------------

    function parsePayload(jsonStr) {
        try {
            duration = _extractNumber(jsonStr, "\"d\":");
            toolCalls = _extractNumber(jsonStr, "\"t\":");
            filesEdited = _extractNumber(jsonStr, "\"e\":");
            bashCount = _extractNumber(jsonStr, "\"b\":");
            idleSeconds = _extractNumber(jsonStr, "\"i\":");

            var secs = TextUtil.extractSignedNumber(jsonStr, "\"s\":", null);
            if (secs == null || secs < 0) {
                lastCompletion = "never";
            } else {
                lastCompletion = StatsModel.formatDuration(secs) + " ago";
            }

            var pid = _extractNumber(jsonStr, "\"p\":");
            if (pid == 1) {
                provider = "C";
            } else if (pid == 2) {
                provider = "O";
            } else {
                provider = "";
            }
        } catch (e) {
            // Silently ignore parse errors — stale display is better than crash.
        }
    }

    // Extract an unsigned numeric value following the given key prefix.
    function _extractNumber(str, key) {
        var n = TextUtil.extractSignedNumber(str, key, null);
        return n == null ? 0 : n;
    }

}

module StatsModel {
    // Per-provider instances. Constructed lazily on first access so that the
    // module load order is safe.
    var claude = new StatsData();
    var opencode = new StatsData();

    // -----------------------------------------------------------------------
    // formatDuration — convert seconds to a human-readable string
    // -----------------------------------------------------------------------

    function formatDuration(secs) {
        if (secs < 60) {
            return secs.toString() + "s";
        }
        if (secs < 3600) {
            return (secs / 60).toString() + "m";
        }
        return (secs / 3600).toString() + "h";
    }
}
