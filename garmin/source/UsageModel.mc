// UsageModel.mc — weekly Claude + Codex quota state for the usage view.
//
// The desktop daemon pushes a compact JSON payload on PROVIDER_USAGE_CHAR_UUID:
//
//   {"c":23,"x":41}
//
//   c  Claude 7-day used percentage (0..100, -1 = unknown/absent)
//   x  Codex  7-day used percentage (0..100, -1 = unknown/absent)
//
// The released USAGE_CHAR_UUID remains a fallback for old-daemon rollouts;
// its "w" key updates Claude while Codex remains unknown.
//
// -1 means the data is unavailable (API-key users, or before the first API
// response in a session) — the view draws an empty bar and no trailing value.

using Toybox.Lang;

module UsageModel {
    // 10-cell bars: each filled cell represents 10%.
    const BAR_CELLS = 10;

    var claudePct = -1;
    var codexPct = -1;

    // Number of filled cells (0..BAR_CELLS) for a percentage; -1 stays 0.
    function filledCells(pct) {
        if (pct < 0) {
            return 0;
        }
        var n = (pct * BAR_CELLS + 50) / 100; // round to nearest cell
        if (n < 0) {
            return 0;
        }
        if (n > BAR_CELLS) {
            return BAR_CELLS;
        }
        return n;
    }

    // Update state from a compact JSON payload (keys "c", "x"; -1 = absent).
    function parsePayload(jsonStr) {
        try {
            claudePct = TextUtil.extractSignedNumber(jsonStr, "\"c\":", -1);
            codexPct = TextUtil.extractSignedNumber(jsonStr, "\"x\":", -1);
        } catch (e) {
            // Stale display beats a crash.
        }
    }

    // Released daemons expose Claude's weekly value as "w". Use it as a
    // fallback when the provider characteristic is absent during rollout.
    function parseLegacyPayload(jsonStr) {
        try {
            claudePct = TextUtil.extractSignedNumber(jsonStr, "\"w\":", -1);
        } catch (e) {
            // Stale display beats a crash.
        }
    }

}
