// BleManager.mc — BLE GATT profile registration and central delegate.
//
// This file contains:
//   1. registerBleProfile()  — registers the custom service/characteristics
//      with the Garmin BLE stack so the watch can discover them.
//   2. OhMyWristBleDelegate     — handles scan results, connection state changes,
//      and characteristic notifications from the desktop daemon.
//
// UUID constants must match protocol.py on the desktop side exactly.
//
// HISTORY_CHAR_UUID carries one binary frame per event (see
// HistoryDecoder.mc).  ALERT_CHAR_UUID triggers a vibration pattern.
// STATS_CLAUDE/OPENCODE_CHAR_UUID are per-provider session JSON.

using Toybox.BluetoothLowEnergy as BLE;
using Toybox.Attention;
using Toybox.Lang;
using Toybox.StringUtil;
using Toybox.System;
using Toybox.Timer;
using Toybox.WatchUi;

// ---------------------------------------------------------------------------
// UUID constants — must match protocol.py
// ---------------------------------------------------------------------------

var OHM_SERVICE_UUID = ConnectionIdModel.serviceUuidForCurrentId();
var HISTORY_CHAR_UUID = BLE.stringToUuid(
    "0FA155B1-0C21-723A-970C-9821F1C5FFAB"
);
var SESSION_CHAR_UUID = BLE.stringToUuid(
    "0FA155B2-0C21-723A-970C-9821F1C5FFAB"
);
var ALERT_CHAR_UUID = BLE.stringToUuid("0FA155B3-0C21-723A-970C-9821F1C5FFAB");
var STATS_CLAUDE_CHAR_UUID = BLE.stringToUuid(
    "0FA155B4-0C21-723A-970C-9821F1C5FFAB"
);
var STATS_OPENCODE_CHAR_UUID = BLE.stringToUuid(
    "0FA155B5-0C21-723A-970C-9821F1C5FFAB"
);
var USAGE_CHAR_UUID = BLE.stringToUuid("0FA155B6-0C21-723A-970C-9821F1C5FFAB");
var PROVIDER_USAGE_CHAR_UUID = BLE.stringToUuid(
    "0FA155B7-0C21-723A-970C-9821F1C5FFAB"
);

function refreshOhmServiceUuid() {
    OHM_SERVICE_UUID = ConnectionIdModel.serviceUuidForCurrentId();
    System.println(
        "BLE: using connection_id=" +
            ConnectionIdModel.getId() +
            " service=" +
            ConnectionIdModel.serviceUuidStringForId(ConnectionIdModel.getId())
    );
}

// ---------------------------------------------------------------------------
// Connection state-machine phases and watchdog budgets
//
// The BLE state machine on Connect IQ has several "no-callback" failure modes
// (slow service discovery, lost CCCD descriptor writes, stale advertisements
// pointing at a daemon that no longer exists).  A single shared watchdog
// timer below applies a bounded timeout per phase and re-asserts scanning or
// drops the link when a phase overshoots its budget.
// ---------------------------------------------------------------------------

const PHASE_SCANNING = 0;
const PHASE_CONNECTING = 1;
const PHASE_DISCOVERING = 2;
const PHASE_SUBSCRIBING = 3;
const PHASE_READY = 4;

const WATCHDOG_TICK_MS = 2000;
const SCAN_REASSERT_MS = 15000;
const CONNECT_TIMEOUT_MS = 30000;
const DISCOVERY_ATTEMPT_MAX = 12;
const DISCOVERY_ATTEMPT_DELAY = 500;
const DISCOVERY_TIMEOUT_MS = 8000;
const SUBSCRIBE_STEP_TIMEOUT_MS = 5000;
const BLACKLIST_TTL_MS = 10000;

// Two-phase scan: collect matching adverts for this duration before
// picking the best one (strongest RSSI) and pairing.  Avoids latching
// onto the first (potentially stale/weak) advertisement when a
// stronger one arrives a few hundred milliseconds later.
const SCAN_COLLECT_MS = 1500;

// Delay between tearing a link down and the next BLE op (setScanState etc.).
// Back-to-back BLE ops can cause workarea errors; spacing via a short timer
// gives the stack time to drain state after pairDevice / unpairDevice.
const BLE_OP_SPACING_MS = 1000;

function _phaseName(phase) {
    if (phase == PHASE_SCANNING) {
        return "scanning";
    }
    if (phase == PHASE_CONNECTING) {
        return "connecting";
    }
    if (phase == PHASE_DISCOVERING) {
        return "discovering";
    }
    if (phase == PHASE_SUBSCRIBING) {
        return "subscribing";
    }
    if (phase == PHASE_READY) {
        return "ready";
    }
    return "offline";
}

// ---------------------------------------------------------------------------
// Profile registration
// ---------------------------------------------------------------------------

// Register the custom GATT profile with the Garmin BLE stack.
// Must be called once during onStart() before setScanState(SCANNING).
function registerBleProfile() {
    var profile = {
        :uuid => OHM_SERVICE_UUID,
        :characteristics => [
            {
                :uuid => HISTORY_CHAR_UUID,
                :descriptors => [BLE.cccdUuid()],
            },
            {
                :uuid => SESSION_CHAR_UUID,
                :descriptors => [],
            },
            {
                :uuid => ALERT_CHAR_UUID,
                :descriptors => [BLE.cccdUuid()],
            },
            {
                :uuid => STATS_CLAUDE_CHAR_UUID,
                :descriptors => [BLE.cccdUuid()],
            },
            {
                :uuid => STATS_OPENCODE_CHAR_UUID,
                :descriptors => [BLE.cccdUuid()],
            },
            {
                :uuid => USAGE_CHAR_UUID,
                :descriptors => [BLE.cccdUuid()],
            },
            {
                :uuid => PROVIDER_USAGE_CHAR_UUID,
                :descriptors => [BLE.cccdUuid()],
            },
        ],
    };
    BLE.registerProfile(profile);
}

// ---------------------------------------------------------------------------
// Helper: check whether a scan result advertises our service
// ---------------------------------------------------------------------------

function deviceAdvertisesOhmService(result) {
    var iter = result.getServiceUuids();
    if (iter == null) {
        return false;
    }
    var uuid = iter.next();
    while (uuid != null) {
        if (uuid.equals(OHM_SERVICE_UUID)) {
            return true;
        }
        uuid = iter.next();
    }
    return false;
}

// ---------------------------------------------------------------------------
// BLE delegate
// ---------------------------------------------------------------------------

class OhMyWristBleDelegate extends BLE.BleDelegate {
    // Single pre-allocated one-shot timer for all phase-specific operations
    // (boot scan, scan collect, discovery retry, reconnect, abort safety net,
    // disconnect-deferred handling).  These use cases are mutually exclusive
    // by phase — the state machine guarantees they never overlap — so a
    // single Timer object suffices.  Pre-allocated in initialize() to avoid
    // "Too Many Timers" on devices with low timer limits.
    var _opTimer;

    // Retry state for service discovery race
    var _discoveryDevice;
    var _discoveryAttempts;

    // CCCD subscribe queue — Connect IQ rejects overlapping BLE writes, so
    // we issue one requestWrite at a time and advance on onDescriptorWrite.
    var _service;
    var _subscribeQueue as Lang.Array = [];

    // Cached characteristic references — used for dispatch in
    // onCharacteristicChanged by REFERENCE COMPARISON (no getUuid() calls).
    // Avoid getUuid() after disconnect: the native backing is freed before
    // the DISCONNECTED callback fires. Comparing by identity is pure Monkey C.
    var _charHistory;
    var _charAlert;
    var _charStatsClaude;
    var _charStatsOpencode;
    var _charUsage;
    var _charProviderUsage;

    // Connected-device tracking so the app can forcibly drop the link in
    // onStop() via BLE.unpairDevice() — without this the daemon waits 4–10 s
    // for the BLE supervision timeout before noticing we left.
    var _connectedDevice;

    // Track the in-flight CCCD UUID so onDescriptorWrite knows which
    // characteristic's subscription succeeded/failed (the Connect IQ
    // descriptor object alone is not enough to identify it cheaply).
    var _currentSubscribingUuid;

    // UUIDs we've already retried once after a failed CCCD write. Used to
    // bound retries to exactly one attempt per characteristic.
    var _retriedCccds;

    // ------------------------------------------------------------------
    // Watchdog state — see header comment for "Connection state-machine
    // phases and watchdog budgets".
    // ------------------------------------------------------------------

    var _phase; // PHASE_* constant
    var _phaseStartTime; // System.getTimer() ms when phase was set
    var _watchdogTimer; // shared Timer driving _onWatchdogTick
    var _currentSubscribeStartTime; // per-step start for PHASE_SUBSCRIBING

    // ScanResult of the device we last asked pairDevice() about, captured
    // before the call returns so the watchdog can blacklist it if the
    // connect/discover phases time out without ever getting to PHASE_READY.
    // Compared via ScanResult.isSameDevice() (the canonical SDK pattern,
    // see NordicThingy52 sample).
    var _pendingScanResult;

    // Array of {:result => ScanResult, :expiry => Number} entries.
    // Devices whose entry is present and not yet expired are skipped by
    // onScanResults — breaks the ghost-advert loop where the watch keeps
    // re-pairing with a cached advertisement of a dead daemon.
    var _blacklist as Lang.Array<Lang.Dictionary>;

    // Re-entrancy guard for _abortToScanning(). When true, the abort is
    // in-flight (e.g. waiting for a DISCONNECTED callback after unpairDevice).
    // _onDisconnected checks this to avoid scheduling a competing timer.
    var _aborting;

    // Device held for deferred unpair when connection attempt is abandoned
    // before CONNECTED callback arrives (see _abortToScanningExtended).
    var _deferredUnpairDevice;

    // Flag for _onAbortSafetyNet to know whether to use extended delay.
    var _abortSafetyIsHwEx;

    // Snapshot of the _aborting flag at disconnect time. Set in
    // _onDisconnected(), consumed by _onDisconnectDeferred().
    var _disconnectWasAborting;

    // Pending connection ID selected from the settings UI. Applied after any
    // current link is torn down so profile re-registration and rescan happen
    // outside the BLE disconnect callback.
    var _pendingConnectionId;

    // Two-phase scan collection state.
    // During PHASE_SCANNING, matching advertisements are accumulated in
    // _scanCollected for SCAN_COLLECT_MS before the best (highest RSSI)
    // is chosen for pairing.  Each entry is {:result => ScanResult,
    // :rssi => Number or null}.
    var _scanCollected as Lang.Array<Lang.Dictionary> = [];

    function initialize() {
        BleDelegate.initialize();
        _discoveryDevice = null;
        _discoveryAttempts = 0;
        _service = null;
        _subscribeQueue = [];
        _charHistory = null;
        _charAlert = null;
        _charStatsClaude = null;
        _charStatsOpencode = null;
        _charUsage = null;
        _charProviderUsage = null;
        _connectedDevice = null;
        _currentSubscribingUuid = null;
        _retriedCccds = {};
        _phase = PHASE_SCANNING;
        _phaseStartTime = System.getTimer();
        _watchdogTimer = null;
        _currentSubscribeStartTime = null;
        _pendingScanResult = null;
        _blacklist = [];
        _aborting = false;
        _deferredUnpairDevice = null;
        _abortSafetyIsHwEx = false;
        // Pre-allocate the op timer up front to avoid "Too Many Timers"
        // on devices with low OS timer-slot limits.
        _opTimer = new Timer.Timer();
        _disconnectWasAborting = false;
        _pendingConnectionId = null;
        _scanCollected = [];
        StatusModel.setPhase("scanning");
        _startWatchdog();
    }

    // Accessor for OhMyWristApp.onStop so it can call BLE.unpairDevice()
    // and tear down the link immediately when the widget closes.
    function getConnectedDevice() {
        return _connectedDevice;
    }

    // Schedule the first BLE scan after a delay, using the shared _opTimer
    // so OhMyWristApp doesn't need its own Timer.Timer() object.
    function scheduleInitialScan(delayMs) {
        _opTimer.stop();
        _opTimer.start(method(:initializeScan), delayMs, false);
    }

    // Apply a new connection ID from the settings UI. If connected, disconnect
    // first and let _onDisconnectDeferred perform the actual UUID switch.
    function applyConnectionId(connectionId) {
        if (connectionId == ConnectionIdModel.getId()) {
            return true;
        }
        _pendingConnectionId = connectionId;
        _scanCollected = [];

        if (_aborting) {
            System.println(
                "BLE: connection ID change pending during existing abort"
            );
            return true;
        }

        if (_connectedDevice != null) {
            System.println(
                "BLE: connection ID change pending; aborting current link"
            );
            _abortToScanningExtended(false);
            return true;
        }

        _opTimer.stop();
        _applyPendingConnectionIdAndRescan();
        return true;
    }

    // Stop all BLE-related timers. Called by OhMyWristApp.onStop()
    // before any BLE teardown to prevent stale timer callbacks from
    // firing during or after widget shutdown.
    function cleanup() {
        if (_watchdogTimer != null) {
            _watchdogTimer.stop();
            _watchdogTimer = null;
        }
        if (_opTimer != null) {
            _opTimer.stop();
            _opTimer = null;
        }
        _scanCollected = [];
        _pendingConnectionId = null;
    }

    // ------------------------------------------------------------------
    // Phase machine + watchdog
    // ------------------------------------------------------------------

    function _setPhase(phase) {
        if (_phase == phase) {
            return;
        }
        _phase = phase;
        _phaseStartTime = System.getTimer();
        StatusModel.setPhase(_phaseName(phase));
        WatchUi.requestUpdate();
        // Keep watchdog running in ALL phases (including PHASE_READY) so we
        // get heartbeat ticks for crash diagnostics on real hardware.
        _startWatchdog();
    }

    function _startWatchdog() {
        if (_watchdogTimer != null) {
            return;
        }
        _watchdogTimer = new Timer.Timer();
        _watchdogTimer.start(method(:_onWatchdogTick), WATCHDOG_TICK_MS, true);
    }

    // Periodic check that the current phase hasn't exceeded its budget.
    // Each branch implements the recovery for one phase — see the
    // per-phase budgets table in DESIGN.md and the file header comment.
    function _onWatchdogTick() as Void {
        var now = System.getTimer();
        _pruneBlacklist(now);
        var elapsed = now - _phaseStartTime;

        // Heartbeat: only log non-ready phases (for timeout diagnostics).
        if (_phase != PHASE_READY) {
            System.println(
                "BLE: tick phase=" + _phaseName(_phase) + " elapsed=" + elapsed
            );
        }

        if (_phase == PHASE_SCANNING) {
            if (elapsed > SCAN_REASSERT_MS) {
                System.println(
                    "BLE: scan watchdog re-asserting SCAN_STATE_SCANNING"
                );
                try {
                    BLE.setScanState(BLE.SCAN_STATE_SCANNING);
                } catch (e) {
                    System.println("BLE: setScanState threw in watchdog");
                }
                _phaseStartTime = now;
            }
        } else if (_phase == PHASE_CONNECTING) {
            if (elapsed > CONNECT_TIMEOUT_MS) {
                System.println(
                    "BLE: connect watchdog timed out — blacklisting + rescanning"
                );
                _blacklistPending(now);
                _abortToScanning();
            }
        } else if (_phase == PHASE_DISCOVERING) {
            if (elapsed > DISCOVERY_TIMEOUT_MS) {
                System.println(
                    "BLE: discovery watchdog timed out — blacklisting + rescanning"
                );
                _blacklistPending(now);
                _abortToScanning();
            }
        } else if (_phase == PHASE_SUBSCRIBING) {
            if (_currentSubscribeStartTime != null) {
                var sub = now - _currentSubscribeStartTime;
                if (sub > SUBSCRIBE_STEP_TIMEOUT_MS) {
                    System.println(
                        "BLE: subscribe-step watchdog timed out — dropping link"
                    );
                    _abortToScanning();
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Blacklist of recently-failed devices
    // ------------------------------------------------------------------

    function _isBlacklisted(scanResult, now) {
        if (scanResult == null) {
            return false;
        }
        for (var i = _blacklist.size() - 1; i >= 0; i--) {
            var entry = _blacklist[i];
            if (entry[:expiry] <= now) {
                _blacklist.remove(entry);
            } else if (scanResult.isSameDevice(entry[:result])) {
                return true;
            }
        }
        return false;
    }

    function _pruneBlacklist(now) {
        for (var i = _blacklist.size() - 1; i >= 0; i--) {
            if (_blacklist[i][:expiry] <= now) {
                _blacklist.remove(_blacklist[i]);
            }
        }
    }

    function _blacklistPending(now) {
        if (_pendingScanResult == null) {
            return;
        }
        _blacklist.add({
            :result => _pendingScanResult,
            :expiry => now + BLACKLIST_TTL_MS,
        });
        System.println(
            "BLE: blacklisted device for " + BLACKLIST_TTL_MS + "ms"
        );
        _pendingScanResult = null;
    }

    // Never call unpairDevice and setScanState back-to-back: Garmin's BLE
    // workarea can fail. The disconnect callback or a timer owns the rescan.
    function _abortToScanning() {
        _abortToScanningExtended(false);
    }

    function _abortToScanningExtended(isHardwareException as Lang.Boolean) {
        if (_aborting) {
            System.println(
                "BLE: _abortToScanning re-entrancy blocked (_aborting=true, phase=" +
                    _phaseName(_phase) +
                    ")"
            );
            return;
        }
        _aborting = true;
        var wasPhase = _phase;
        System.println(
            "BLE: _abortToScanning entered (phase=" +
                _phaseName(_phase) +
                ", hasDevice=" +
                (_connectedDevice != null) +
                ", hwEx=" +
                isHardwareException +
                ")"
        );

        _opTimer.stop();
        _scanCollected = [];
        _service = null;
        _charHistory = null;
        _charAlert = null;
        _charStatsClaude = null;
        _charStatsOpencode = null;
        _charUsage = null;
        _charProviderUsage = null;
        _subscribeQueue = [];
        _discoveryAttempts = 0;
        _currentSubscribingUuid = null;
        _currentSubscribeStartTime = null;
        _retriedCccds = {};

        if (_connectedDevice != null) {
            if (wasPhase == PHASE_CONNECTING) {
                System.println(
                    "BLE: _abortToScanning (never got CONNECTED callback)"
                );
                // A pending connection hides the device from later scans, but
                // synchronous unpair here can crash the native BLE layer.
                _deferredUnpairDevice = _connectedDevice;
                _connectedDevice = null;
                _discoveryDevice = null;
                StatusModel.setConnected(false);
                _setPhase(PHASE_SCANNING);
                _opTimer.stop();
                _opTimer.start(
                    method(:_onDeferredUnpairAndRescan),
                    BLE_OP_SPACING_MS,
                    false
                );
                _aborting = false;
                return;
            }

            System.println(
                "BLE: _abortToScanning calling unpairDevice, expecting DISCONNECTED callback"
            );
            try {
                BLE.unpairDevice(_connectedDevice);
                // Safety net: if DISCONNECTED callback never fires, force-reset
                // after a bounded timeout to prevent permanent stuck state.
                _scheduleAbortSafetyNet(isHardwareException);
            } catch (e) {
                System.println(
                    "BLE: unpairDevice threw in _abortToScanning: " +
                        e.getErrorMessage()
                );
                _connectedDevice = null;
                _discoveryDevice = null;
                StatusModel.setConnected(false);
                _setPhase(PHASE_SCANNING);
                _aborting = false;
                if (_pendingConnectionId != null) {
                    _applyPendingConnectionIdAndRescan();
                    return;
                }
                _scheduleRescanExtended(isHardwareException);
            }
            return;
        }

        System.println(
            "BLE: _abortToScanning no device, going straight to scan"
        );
        _discoveryDevice = null;
        StatusModel.setConnected(false);
        _setPhase(PHASE_SCANNING);
        _aborting = false;
        if (_pendingConnectionId != null) {
            _applyPendingConnectionIdAndRescan();
            return;
        }
        _scheduleRescanExtended(isHardwareException);
    }

    function _scheduleRescanExtended(isHardwareException as Lang.Boolean) {
        // If we just recovered from a hardware crash/throw, step back for 3 seconds
        // instead of 1 second to let the C++ Workarea connections settle down.
        var delay = isHardwareException
            ? BLE_OP_SPACING_MS * 3
            : BLE_OP_SPACING_MS;
        _opTimer.stop();
        _opTimer.start(method(:restartScan), delay, false);
    }

    // Safety net: if unpairDevice() does not produce a DISCONNECTED callback
    // within 5 seconds (e.g. because the device was never truly connected),
    // force-reset state so the system doesn't get stuck permanently.
    function _scheduleAbortSafetyNet(isHardwareException as Lang.Boolean) {
        _abortSafetyIsHwEx = isHardwareException;
        _opTimer.stop();
        _opTimer.start(method(:_onAbortSafetyNet), 5000, false);
    }

    function _onAbortSafetyNet() as Void {
        if (!_aborting) {
            // Callback arrived in time, nothing to do.
            return;
        }
        System.println(
            "BLE: SAFETY NET — no DISCONNECTED callback after unpair, force-resetting state"
        );
        // Force-clear everything as if _onDisconnected had fired.
        _connectedDevice = null;
        _discoveryDevice = null;
        _service = null;
        _subscribeQueue = [];
        _scanCollected = [];
        // _opTimer will be restarted by _scheduleRescanExtended below.
        _currentSubscribingUuid = null;
        _currentSubscribeStartTime = null;
        _retriedCccds = {};
        StatusModel.setConnected(false);
        _setPhase(PHASE_SCANNING);
        _aborting = false;
        if (_pendingConnectionId != null) {
            _applyPendingConnectionIdAndRescan();
            return;
        }
        _scheduleRescanExtended(_abortSafetyIsHwEx);
    }

    // Rearm scanning via Timer rather than calling setScanState inline so
    // that any prior BLE op (unpair, failed pair) has time to drain before
    // the next one lands.
    function _scheduleRescan() {
        _opTimer.stop();
        _opTimer.start(method(:restartScan), BLE_OP_SPACING_MS, false);
    }

    function _applyPendingConnectionIdAndRescan() as Void {
        if (_pendingConnectionId == null) {
            _scheduleRescan();
            return;
        }

        var id = _pendingConnectionId;
        _pendingConnectionId = null;

        try {
            BLE.setScanState(BLE.SCAN_STATE_OFF);
        } catch (e) {
            // Best effort: the next scheduled scan will reassert SCANNING.
        }

        ConnectionIdModel.setId(id);
        refreshOhmServiceUuid();

        try {
            registerBleProfile();
        } catch (e) {
            System.println(
                "BLE: registerProfile failed after connection ID change: " +
                    e.getErrorMessage()
            );
            StatusModel.setPhase("error");
            WatchUi.requestUpdate();
            return;
        }

        _service = null;
        _charHistory = null;
        _charAlert = null;
        _charStatsClaude = null;
        _charStatsOpencode = null;
        _charUsage = null;
        _charProviderUsage = null;
        _discoveryDevice = null;
        _connectedDevice = null;
        _pendingScanResult = null;
        _subscribeQueue = [];
        _scanCollected = [];
        _discoveryAttempts = 0;
        _currentSubscribingUuid = null;
        _currentSubscribeStartTime = null;
        _retriedCccds = {};
        StatusModel.setConnected(false);
        _setPhase(PHASE_SCANNING);
        WatchUi.requestUpdate();
        _scheduleRescan();
    }

    // The deferred unpair frees a pending slot after the BLE stack settles.
    // A late CONNECTED callback cancels it so a live connection is preserved.
    function _onDeferredUnpairAndRescan() as Void {
        if (_deferredUnpairDevice != null) {
            var dev = _deferredUnpairDevice;
            _deferredUnpairDevice = null;
            System.println(
                "BLE: attempting deferred unpairDevice on never-connected device"
            );
            try {
                BLE.unpairDevice(dev);
                System.println(
                    "BLE: deferred unpairDevice succeeded (pending connection freed)"
                );
            } catch (e) {
                System.println(
                    "BLE: deferred unpairDevice threw: " + e.getErrorMessage()
                );
                // OS supervision timeout will eventually free the slot.
            }
        }

        // Guard: if we're no longer in PHASE_SCANNING (e.g., a late
        // CONNECTED callback was accepted via Case 3), don't rescan.
        if (_phase != PHASE_SCANNING) {
            System.println(
                "BLE: _onDeferredUnpairAndRescan skipping rescan (phase=" +
                    _phaseName(_phase) +
                    ")"
            );
            return;
        }

        if (_pendingConnectionId != null) {
            _applyPendingConnectionIdAndRescan();
            return;
        }

        // Space the rescan from the unpair to let the BLE stack settle.
        _opTimer.stop();
        _opTimer.start(method(:restartScan), BLE_OP_SPACING_MS, false);
    }

    // Stop scanning before pairDevice: pairing while scanning triggers an
    // intermittent Garmin workarea race. Collect first to choose by RSSI.
    function onScanResults(scanResults) as Void {
        // Only act on scan results when we're actively looking for a device.
        if (_phase != PHASE_SCANNING) {
            return;
        }

        var now = System.getTimer();
        var total = 0;
        var matched = 0;
        var result = scanResults.next();
        while (result != null) {
            // Type guard: on some older CIQ runtimes the scan results
            // iterator can yield non-ScanResult objects.
            if (!(result instanceof BLE.ScanResult)) {
                result = scanResults.next();
                continue;
            }
            total++;
            if (deviceAdvertisesOhmService(result)) {
                matched++;
                var scanResult = result as BLE.ScanResult;
                if (_isBlacklisted(scanResult, now)) {
                    System.println("BLE: skipping blacklisted advert");
                    result = scanResults.next();
                    continue;
                }

                // Read RSSI; null means unavailable on this firmware.
                var rssi = null;
                try {
                    rssi = scanResult.getRssi();
                } catch (e) {
                    // getRssi unavailable — fall through with null
                }

                // Skip adverts below the minimum threshold.
                if (rssi != null && rssi < -85) {
                    System.println("BLE: skipping weak advert rssi=" + rssi);
                    result = scanResults.next();
                    continue;
                }

                // Start the collection timer on the first match.
                // _opTimer doubles as the scan-collect timer; if it's
                // already running for this purpose the new advert is
                // accumulated in _scanCollected and the existing window
                // stays unchanged. Check size BEFORE adding so the
                // guard fires exactly once per window.
                var isFirstMatch = _scanCollected.size() == 0;

                // Add or update this device in the collection array.
                // If the same device advertises again during the window,
                // keep the entry with the stronger (more recent) RSSI.
                _collectScanResult(scanResult, rssi);

                if (isFirstMatch) {
                    System.println(
                        "BLE: scan collect window opened (" +
                            SCAN_COLLECT_MS +
                            "ms)"
                    );
                    _opTimer.stop();
                    _opTimer.start(
                        method(:_onScanCollectDone),
                        SCAN_COLLECT_MS,
                        false
                    );
                }
            }
            result = scanResults.next();
        }
        if (total > 0) {
            System.println(
                "BLE: onScanResults total=" +
                    total +
                    " matched=" +
                    matched +
                    " collected=" +
                    _scanCollected.size()
            );
        }
    }

    // Add or update a scan result in the collection array.
    // If the same device is already present, keep the stronger RSSI.
    function _collectScanResult(scanResult as BLE.ScanResult, rssi) {
        for (var i = 0; i < _scanCollected.size(); i++) {
            var entry = _scanCollected[i];
            if (scanResult.isSameDevice(entry[:result])) {
                // Update if the new RSSI is stronger (or the old was null).
                var oldRssi = entry[:rssi];
                if (oldRssi == null || (rssi != null && rssi > oldRssi)) {
                    _scanCollected[i] = {
                        :result => scanResult,
                        :rssi => rssi,
                    };
                }
                return;
            }
        }
        _scanCollected.add({ :result => scanResult, :rssi => rssi });
        System.println(
            "BLE: collected device #" + _scanCollected.size() + " rssi=" + rssi
        );
    }

    // Phase 2: collection window closed — pick the best candidate and pair.
    function _onScanCollectDone() as Void {
        // Guard: if we left PHASE_SCANNING while the timer was pending
        // (e.g. widget closing), discard the collected results.
        if (_phase != PHASE_SCANNING) {
            System.println(
                "BLE: scan collect done but phase=" +
                    _phaseName(_phase) +
                    ", discarding"
            );
            _scanCollected = [];
            return;
        }

        if (_scanCollected.size() == 0) {
            System.println("BLE: scan collect done with 0 candidates");
            return;
        }

        // Pick the candidate with the strongest RSSI.  Entries where RSSI
        // is null (unavailable on that firmware) sort below any numeric
        // value so they are only chosen as a last resort.
        var bestIdx = 0;
        var bestRssi = _scanCollected[0][:rssi];
        var candidateCount = _scanCollected.size();
        for (var i = 1; i < candidateCount; i++) {
            var r = _scanCollected[i][:rssi];
            if (bestRssi == null || (r != null && r > bestRssi)) {
                bestIdx = i;
                bestRssi = r;
            }
        }
        var best = _scanCollected[bestIdx];
        _scanCollected = [];

        var scanResult = best[:result] as BLE.ScanResult;
        System.println(
            "BLE: scan collect done — picked #" +
                (bestIdx + 1) +
                " of " +
                candidateCount +
                " candidates, rssi=" +
                bestRssi
        );

        // Re-check blacklist — the device could have been blacklisted
        // while the collection window was open.
        if (_isBlacklisted(scanResult, System.getTimer())) {
            System.println(
                "BLE: best candidate is now blacklisted, discarding"
            );
            return;
        }

        // Guard: verify a connection slot is available.
        try {
            if (BLE.getAvailableConnectionCount() <= 0) {
                System.println(
                    "BLE: no available connections at collect-done, deferring"
                );
                return;
            }
        } catch (e) {
            System.println(
                "BLE: getAvailableConnectionCount threw at collect-done"
            );
        }

        // Transition to CONNECTING *before* the BLE ops so the
        // watchdog bounds the wait even if no callback ever fires.
        _pendingScanResult = scanResult;
        _setPhase(PHASE_CONNECTING);

        // Log RSSI for diagnostics
        var pairRssi = "N/A";
        try {
            var r = scanResult.getRssi();
            if (r != null) {
                pairRssi = r.toString();
            }
        } catch (e) {}
        System.println(
            "BLE: pairing with device (rssi=" +
                pairRssi +
                ", aborting=" +
                _aborting +
                ")"
        );

        // Stop the scan, then pair — exactly the SDK sample's sequence.
        try {
            BLE.setScanState(BLE.SCAN_STATE_OFF);
        } catch (e) {
            System.println("BLE: setScanState(OFF) threw before pair");
            _abortToScanningExtended(true);
            return;
        }
        try {
            _connectedDevice = BLE.pairDevice(scanResult);
        } catch (e) {
            System.println("BLE: pairDevice threw synchronously");
            _connectedDevice = null;
            _abortToScanningExtended(true);
            return;
        }
        WatchUi.requestUpdate();
    }

    // ------------------------------------------------------------------
    // Scan state confirmation
    // ------------------------------------------------------------------

    // Called by the BLE stack after setScanState() completes. Lets us
    // confirm the radio actually transitioned and provides a status code
    // when it silently fails. The existing watchdog re-asserts scanning
    // every SCAN_REASSERT_MS; this callback resets the watchdog's phase
    // timer on confirmed success so we don't re-assert unnecessarily.
    function onScanStateChange(scanState, status) as Void {
        System.println(
            "BLE: onScanStateChange state=" + scanState + " status=" + status
        );
        if (status != BLE.STATUS_SUCCESS) {
            System.println(
                "BLE: scan state change FAILED status=" +
                    status +
                    " phase=" +
                    _phaseName(_phase)
            );
            // Let the watchdog handle retry — no new recovery path here.
            return;
        }
        // Confirmed scan start: reset the watchdog phase timer so the
        // SCAN_REASSERT_MS budget counts from the confirmed start, not
        // from whenever _setPhase(PHASE_SCANNING) was called.
        if (scanState == BLE.SCAN_STATE_SCANNING && _phase == PHASE_SCANNING) {
            _phaseStartTime = System.getTimer();
        }
    }

    // ------------------------------------------------------------------
    // Profile registration confirmation
    // ------------------------------------------------------------------

    // Called by the BLE stack after registerProfile() completes.  A
    // silent registration failure means the watch will never receive
    // characteristic notifications — surface it as an "error" phase so
    // the UI can tell the user something is wrong.  Consistent with the
    // synchronous-exception path in OhMyWristApp.onStart (line 46).
    function onProfileRegister(uuid, status) as Void {
        System.println(
            "BLE: onProfileRegister uuid=" + uuid + " status=" + status
        );
        if (status != BLE.STATUS_SUCCESS) {
            System.println(
                "BLE: FATAL — profile registration failed, BLE will not work"
            );
            StatusModel.setPhase("error");
            WatchUi.requestUpdate();
        }
    }

    // ------------------------------------------------------------------
    // Connection state
    // ------------------------------------------------------------------
    function onConnectedStateChanged(device, state) {
        System.println(
            "BLE: onConnectedStateChanged state=" +
                state +
                " phase=" +
                _phaseName(_phase) +
                " aborting=" +
                _aborting +
                " hasDevice=" +
                (_connectedDevice != null)
        );

        // Cancel safety net timer — the callback arrived.
        // (_opTimer may be running as the abort safety net; stopping it
        // is safe and idempotent — the next phase transition will restart
        // it for its new purpose.)
        _opTimer.stop();

        // Identity filter: ignore callbacks for devices we're not tracking.
        // Case 1: We have a tracked device and this callback is for a different one.
        if (_connectedDevice != null && device != _connectedDevice) {
            System.println("BLE: ignoring callback for non-tracked device");
            return;
        }
        // Case 2: We have NO tracked device and this is a DISCONNECT callback.
        // This is a stale callback from a previous unpairDevice call — the
        // device was already cleaned up by _onDisconnected(). Ignore it to
        // prevent re-triggering the disconnect/unpair cycle.
        if (
            _connectedDevice == null &&
            state != BLE.CONNECTION_STATE_CONNECTED
        ) {
            System.println(
                "BLE: ignoring stale DISCONNECTED (no tracked device)"
            );
            return;
        }
        // Case 3: Late CONNECTED callback after we abandoned the connection
        // (e.g. connect timeout fired, we deferred unpairDevice). If we're
        // idle in PHASE_SCANNING, accept the connection — it completed slowly
        // but is still valid. Cancel any pending deferred unpair.
        // If we're already in a different phase (e.g. connecting to a new
        // device), drop this stale connection.
        if (
            _connectedDevice == null &&
            state == BLE.CONNECTION_STATE_CONNECTED
        ) {
            if (_phase == PHASE_SCANNING) {
                System.println(
                    "BLE: late CONNECTED callback — accepting (phase=scanning)"
                );
                // Cancel the deferred unpair — we're keeping this connection.
                _deferredUnpairDevice = null;
                // Cancel any pending rescan/boot/collect timer.
                _opTimer.stop();
                // Stop scanning — we have a connection now.
                try {
                    BLE.setScanState(BLE.SCAN_STATE_OFF);
                } catch (e) {}
                _connectedDevice = device;
                _pendingScanResult = null;
                // Set phase to CONNECTING momentarily so _onConnected's
                // stale-guard passes, then it transitions to DISCOVERING.
                _setPhase(PHASE_CONNECTING);
                _onConnected(device);
            } else {
                System.println(
                    "BLE: late CONNECTED callback — rejecting (phase=" +
                        _phaseName(_phase) +
                        ")"
                );
                try {
                    BLE.unpairDevice(device);
                } catch (e) {
                    System.println("BLE: unpairDevice threw on late CONNECTED");
                }
            }
            return;
        }

        if (state == BLE.CONNECTION_STATE_CONNECTED) {
            _onConnected(device);
        } else {
            // Link lost (out of range, daemon removed us, supervision timeout).
            //
            // IMPORTANT: Do NOT call unpairDevice() here. When the OS delivers
            // a DISCONNECTED callback for a link-loss event, the connection
            // slot is already freed by the OS. Calling unpairDevice() on an
            // already-disconnected device crashes the native BLE stack
            // (the device object is invalidated).
            //
            // unpairDevice() is only needed when WE initiate the disconnect
            // (via _abortToScanning), not when the remote side drops us.
            _onDisconnected();
        }
    }

    function _onConnected(device) {
        var connectDuration = System.getTimer() - _phaseStartTime;
        System.println("BLE: CONNECTED after " + connectDuration + "ms");

        // If the watchdog gave up on this pairing already and moved us back
        // to scanning, the late CONNECTED callback is stale — drop the link
        // immediately instead of proceeding into discovery.
        if (_phase != PHASE_CONNECTING) {
            System.println(
                "BLE: stale onConnected (phase=" +
                    _phaseName(_phase) +
                    "), unpairing"
            );
            // Mark as aborting so the DISCONNECTED callback uses the
            // spaced _scheduleRescan() path instead of creating a competing
            // 5 s reconnect timer.
            _aborting = true;
            try {
                BLE.unpairDevice(device);
            } catch (e) {
                _aborting = false;
            }
            return;
        }

        // Secondary gate: verify the device is actually connected. Catches
        // the rare edge case where the OS delivers CONNECTION_STATE_CONNECTED
        // but the device immediately drops before we start discovery.
        try {
            if (!device.isConnected()) {
                System.println(
                    "BLE: device not actually connected in _onConnected, bailing"
                );
                return;
            }
        } catch (e) {
            // isConnected() threw — device object likely invalidated.
            System.println("BLE: device.isConnected() threw in _onConnected");
            return;
        }

        // GATT service discovery is asynchronous on Connect IQ — `getService`
        // can return null for several seconds after CONNECTION_STATE_CONNECTED.
        // Retry up to DISCOVERY_ATTEMPT_MAX × DISCOVERY_ATTEMPT_DELAY ms.
        _discoveryDevice = device;
        _connectedDevice = device;
        _discoveryAttempts = 0;
        _setPhase(PHASE_DISCOVERING);
        _tryDiscoverService();
    }

    function _tryDiscoverService() as Void {
        var device = _discoveryDevice;
        if (device == null) {
            return;
        }

        var service = null;
        try {
            service = device.getService(OHM_SERVICE_UUID);
        } catch (e) {
            // Device object invalidated (disconnected during discovery).
            System.println("BLE: getService threw (device invalidated?)");
            return;
        }
        if (service == null) {
            _discoveryAttempts++;
            if (_discoveryAttempts < DISCOVERY_ATTEMPT_MAX) {
                _opTimer.stop();
                _opTimer.start(
                    method(:_tryDiscoverService),
                    DISCOVERY_ATTEMPT_DELAY,
                    false
                );
                return;
            }
            // Discovery exhausted — blacklist this device so the next
            // scan won't immediately re-latch onto the same ghost advert,
            // then return to scanning straight away.
            System.println(
                "BLE: service discovery exhausted, blacklisting + rescanning"
            );
            _blacklistPending(System.getTimer());
            _abortToScanning();
            return;
        }

        // Connect IQ permits only ONE outstanding BLE write at a time.
        // Queue the CCCD subscribes; each one fires on the previous
        // onDescriptorWrite callback.
        _service = service;
        _retriedCccds = {};
        _subscribeQueue = [
            HISTORY_CHAR_UUID,
            ALERT_CHAR_UUID,
            STATS_CLAUDE_CHAR_UUID,
            STATS_OPENCODE_CHAR_UUID,
            USAGE_CHAR_UUID,
            PROVIDER_USAGE_CHAR_UUID,
        ];
        _setPhase(PHASE_SUBSCRIBING);
        _subscribeNext();

        StatusModel.setConnected(true);
    }

    function _subscribeNext() as Void {
        if (_subscribeQueue.size() == 0 || _service == null) {
            _currentSubscribeStartTime = null;
            return;
        }
        var charUuid = _subscribeQueue[0];
        _subscribeQueue = _subscribeQueue.slice(1, null);

        var ch = null;
        var cccd = null;
        try {
            ch = _service.getCharacteristic(charUuid);
            if (ch != null) {
                cccd = ch.getDescriptor(BLE.cccdUuid());
                // Cache the characteristic reference for dispatch by identity
                // in onCharacteristicChanged (avoids getUuid() native crash).
                if (charUuid.equals(HISTORY_CHAR_UUID)) {
                    _charHistory = ch;
                } else if (charUuid.equals(ALERT_CHAR_UUID)) {
                    _charAlert = ch;
                } else if (charUuid.equals(STATS_CLAUDE_CHAR_UUID)) {
                    _charStatsClaude = ch;
                } else if (charUuid.equals(STATS_OPENCODE_CHAR_UUID)) {
                    _charStatsOpencode = ch;
                } else if (charUuid.equals(USAGE_CHAR_UUID)) {
                    _charUsage = ch;
                } else if (charUuid.equals(PROVIDER_USAGE_CHAR_UUID)) {
                    _charProviderUsage = ch;
                }
            }
        } catch (e) {
            // Service/characteristic invalidated (device disconnecting).
            System.println(
                "BLE: _subscribeNext service access threw (disconnecting?)"
            );
            return;
        }
        if (ch == null || cccd == null) {
            _subscribeNext();
            return;
        }
        _currentSubscribingUuid = charUuid;
        _currentSubscribeStartTime = System.getTimer();
        try {
            cccd.requestWrite([0x01, 0x00]b);
        } catch (e) {
            // Skip and try the next one; do not crash the app.
            System.println("BLE: CCCD requestWrite threw for " + charUuid);
            _currentSubscribingUuid = null;
            _subscribeNext();
        }
    }

    function _onDisconnected() {
        System.println(
            "BLE: _onDisconnected (aborting=" +
                _aborting +
                ", phase=" +
                _phaseName(_phase) +
                ")"
        );

        // Allocations, UI calls, and scan changes in this callback can corrupt
        // Garmin's BLE workarea. Only clear references and reuse _opTimer here.

        _service = null;
        _charHistory = null;
        _charAlert = null;
        _charStatsClaude = null;
        _charStatsOpencode = null;
        _charUsage = null;
        _charProviderUsage = null;
        _discoveryDevice = null;
        _connectedDevice = null;
        _currentSubscribingUuid = null;
        _currentSubscribeStartTime = null;

        _disconnectWasAborting = _aborting;
        _aborting = false;

        _opTimer.stop();
        _opTimer.start(method(:_onDisconnectDeferred), 200, false);
    }

    // Deferred disconnect handler — runs ~200ms after the OS delivers the
    // DISCONNECTED callback. Safe to do allocations, UI updates, and start
    // timers here because we're outside the BLE callback context.
    function _onDisconnectDeferred() as Void {
        System.println(
            "BLE: _onDisconnectDeferred firing (wasAborting=" +
                _disconnectWasAborting +
                ")"
        );

        // Reset remaining connection-scoped state (involves allocations).
        _subscribeQueue = [];
        _scanCollected = [];
        _discoveryAttempts = 0;
        _retriedCccds = {};

        StatusModel.setConnected(false);
        _setPhase(PHASE_SCANNING);
        WatchUi.requestUpdate();

        if (_pendingConnectionId != null) {
            _applyPendingConnectionIdAndRescan();
            return;
        }

        // If disconnect was triggered by _abortToScanning (or stale-connect
        // cleanup), use the standard spaced rescan path.
        if (_disconnectWasAborting) {
            _scheduleRescan();
            return;
        }

        // Attempt to reconnect after 5 seconds. The watchdog's
        // SCAN_REASSERT_MS will keep nudging the radio if this initial
        // attempt silently fails.
        _opTimer.stop();
        _opTimer.start(method(:restartScan), 5000, false);
    }

    // Some CIQ firmware calls this undocumented delegate hook. Keep it because
    // removing it caused connection timeouts and native runtime crashes.
    function onStart(state) {
        // Guard: only run boot sequence during initial scanning phase.
        // On some CIQ runtimes this callback fires late or re-fires,
        // which would stomp _opTimer and call setScanState(SCANNING)
        // during an active PHASE_CONNECTING — disrupting the pairing
        // handshake.
        if (_phase != PHASE_SCANNING) {
            System.println(
                "BLE: onStart ignored (phase=" + _phaseName(_phase) + ")"
            );
            return;
        }

        registerBleProfile();

        _opTimer.stop();
        _opTimer.start(method(:initializeScan), 2000, false);
    }

    function initializeScan() as Void {
        // Phase guard: if the state machine has moved past scanning
        // (e.g. we're already connecting), do NOT restart the scan —
        // setScanState(SCANNING) during PHASE_CONNECTING disrupts
        // the BLE pairing at the HCI level.
        if (_phase != PHASE_SCANNING) {
            System.println(
                "BLE: initializeScan skipped (phase=" + _phaseName(_phase) + ")"
            );
            return;
        }
        try {
            Toybox.BluetoothLowEnergy.setScanState(
                Toybox.BluetoothLowEnergy.SCAN_STATE_SCANNING
            );
        } catch (e) {
            System.println(
                "BLE: Boot scan failed, letting watchdog handle it."
            );
        }
    }

    // Timer callback: restart BLE scanning.
    // Phase-gated no-op: if we are no longer in PHASE_SCANNING when the
    // timer fires, return immediately.
    function restartScan() as Void {
        if (_phase != PHASE_SCANNING) {
            System.println(
                "BLE: restartScan no-op (phase=" + _phaseName(_phase) + ")"
            );
            return;
        }
        System.println(
            "BLE: restartScan firing (aborting=" +
                _aborting +
                ", blacklist=" +
                _blacklist.size() +
                ")"
        );

        try {
            var avail = BLE.getAvailableConnectionCount();
            System.println("BLE: availableConnectionCount=" + avail);
            if (avail <= 0) {
                System.println(
                    "BLE: restartScan deferred (no available connections)"
                );
                // Use 5s delay — the OS supervision timeout needs time to
                // free the occupied slot. This prevents rapid re-check loops.
                _opTimer.stop();
                _opTimer.start(method(:restartScan), 5000, false);
                return;
            }
        } catch (e) {
            System.println(
                "BLE: getAvailableConnectionCount threw in restartScan. Backing off."
            );
            _scheduleRescanExtended(true);
            return;
        }

        try {
            BLE.setScanState(BLE.SCAN_STATE_SCANNING);
        } catch (e) {
            System.println(
                "BLE: setScanState threw in restartScan. Stack busy. Backing off."
            );
            // CRITICAL FIX: If the OS rejects scanning, defer back out with an extended delay
            // instead of allowing a crash or locking up the state machine.
            _scheduleRescanExtended(true);
        }
    }

    // A queued post-disconnect notification may have freed native backing.
    // Dispatch by cached reference: getUuid() can cause an uncatchable crash.
    function onCharacteristicChanged(characteristic, value) {
        if (_charStatsClaude == null) {
            return;
        }

        if (characteristic == _charStatsClaude) {
            var sc = _decodeUtf8(value);
            if (sc != null) {
                StatsModel.claude.parsePayload(sc);
                WatchUi.requestUpdate();
            }
        } else if (characteristic == _charStatsOpencode) {
            var so = _decodeUtf8(value);
            if (so != null) {
                StatsModel.opencode.parsePayload(so);
                WatchUi.requestUpdate();
            }
        } else if (characteristic == _charUsage) {
            var su = _decodeUtf8(value);
            if (su != null) {
                UsageModel.parseLegacyPayload(su);
                WatchUi.requestUpdate();
            }
        } else if (characteristic == _charProviderUsage) {
            var pu = _decodeUtf8(value);
            if (pu != null) {
                UsageModel.parsePayload(pu);
                WatchUi.requestUpdate();
            }
        } else if (characteristic == _charHistory) {
            var frame = HistoryDecoder.decodeFrame(value);
            if (frame != null) {
                StatusModel.appendEvent(frame);
                WatchUi.requestUpdate();
            }
        } else if (characteristic == _charAlert) {
            if (value != null && value.size() > 0) {
                var alertByte = value[0];
                if (alertByte != 0x00) {
                    triggerHaptic(alertByte);
                }
            }
        }
        // If none match — stale notification from dead connection. Silently drop.
    }

    // ByteArray#toString() returns "[72, 101, ...]" — not a UTF-8 decode.
    // Use StringUtil.convertEncodedString to get the actual string.
    //
    // Defensive: BLE values from onCharacteristicRead/Changed have been
    // observed to be the wrong type or trigger convertEncodedString to throw
    // on some firmwares. A crash here takes down the whole widget, so we
    // treat any decode failure as "ignore this value" and log to the
    // simulator console for diagnosis.
    function _decodeUtf8(bytes) {
        if (bytes == null) {
            return null;
        }
        if (!(bytes instanceof Lang.ByteArray)) {
            System.println("BLE: _decodeUtf8 got non-ByteArray: " + bytes);
            return null;
        }
        if (bytes.size() == 0) {
            return "";
        }
        try {
            return StringUtil.convertEncodedString(bytes, {
                :fromRepresentation => StringUtil.REPRESENTATION_BYTE_ARRAY,
                :toRepresentation
                =>
                StringUtil.REPRESENTATION_STRING_PLAIN_TEXT,
                :encoding => StringUtil.CHAR_ENCODING_UTF8,
            });
        } catch (e) {
            System.println("BLE: _decodeUtf8 threw on size=" + bytes.size());
            return null;
        }
    }

    // ------------------------------------------------------------------
    // Haptic patterns (Feature 1)
    // ------------------------------------------------------------------

    // Triggers a vibration pattern corresponding to the alert type.
    //
    //   0x01 IDLE_WAITING  — two short pulses ("hey, I need you")
    //   0x02 SESSION_DONE  — one long solid buzz ("all done")
    //   0x03 DESTRUCTIVE   — three rapid sharp pulses ("danger")
    //   0x04 AGENT_DONE    — one soft tap (quiet acknowledgement)
    function triggerHaptic(alertType) {
        if (!(Attention has :vibrate)) {
            return;
        }

        var pattern;

        switch (alertType) {
            case 0x01:
                pattern = [
                    new Attention.VibeProfile(80, 200),
                    new Attention.VibeProfile(0, 150),
                    new Attention.VibeProfile(80, 200),
                ];
                break;

            case 0x02:
                pattern = [new Attention.VibeProfile(100, 600)];
                break;

            case 0x03:
                pattern = [
                    new Attention.VibeProfile(100, 120),
                    new Attention.VibeProfile(0, 80),
                    new Attention.VibeProfile(100, 120),
                    new Attention.VibeProfile(0, 80),
                    new Attention.VibeProfile(100, 120),
                ];
                break;

            case 0x04:
                pattern = [new Attention.VibeProfile(60, 150)];
                break;

            default:
                return;
        }

        Attention.vibrate(pattern);
    }

    function onDescriptorWrite(descriptor, status) {
        var uuid = _currentSubscribingUuid;
        _currentSubscribingUuid = null;

        System.println("BLE: CCCD write status=" + status + " uuid=" + uuid);

        // Verify the descriptor, but tolerate freed native backing after loss.
        try {
            if (!BLE.cccdUuid().equals(descriptor.getUuid())) {
                System.println(
                    "BLE: onDescriptorWrite for non-CCCD descriptor, ignoring"
                );
                return;
            }
        } catch (e) {
            System.println(
                "BLE: descriptor.getUuid() threw in onDescriptorWrite (link dropping?)"
            );
        }

        // Never advance a subscription after the disconnect cleared its state.
        if (_service == null || _connectedDevice == null) {
            System.println("BLE: onDescriptorWrite after disconnect, ignoring");
            return;
        }

        // Retry once by string key: BLE.Uuid keys vary across CIQ devices.
        if (status != BLE.STATUS_SUCCESS && uuid != null) {
            var uuidKey = uuid.toString();
            if (!_retriedCccds.hasKey(uuidKey)) {
                System.println("BLE: retrying CCCD subscribe for " + uuidKey);
                _retriedCccds.put(uuidKey, true);
                var retried = [uuid];
                retried.addAll(_subscribeQueue);
                _subscribeQueue = retried;
            }
        }

        if (_subscribeQueue.size() > 0) {
            _subscribeNext();
            return;
        }

        // Queue drained. The daemon's deferred-push-on-subscribe will deliver
        // the most recent values ~1.5s after all subscribes complete; until
        // then the view falls back to its "Idle" placeholder.
        _currentSubscribeStartTime = null;
        _pendingScanResult = null;
        System.println("BLE: all CCCDs done, PHASE_READY");
        _setPhase(PHASE_READY);
        WatchUi.requestUpdate();
    }

    // ------------------------------------------------------------------
    // Characteristic write confirmation
    // ------------------------------------------------------------------

    // Called by the BLE stack after Characteristic.requestWrite()
    // completes.  Currently a diagnostic stub — we don't write to
    // characteristics yet (only CCCD descriptors), but having this
    // callback prevents silent write failures if the profile gains
    // writable characteristics in the future.
    function onCharacteristicWrite(characteristic, status) as Void {
        System.println("BLE: onCharacteristicWrite status=" + status);
    }
}
