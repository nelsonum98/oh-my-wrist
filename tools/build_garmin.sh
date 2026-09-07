#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
JUNGLE="$REPO_ROOT/garmin/monkey.jungle"
MANIFEST="$REPO_ROOT/garmin/manifest.xml"
OUT_DIR="$REPO_ROOT/build/garmin"
LOG_DIR="$OUT_DIR/logs"
APP_NAME="oh-my-wrist"
MAX_FAILURE_LOGS="${GARMIN_BUILD_MAX_FAILURE_LOGS:-5}"

if ! [[ "$MAX_FAILURE_LOGS" =~ ^[0-9]+$ ]]; then
    echo "Warning: GARMIN_BUILD_MAX_FAILURE_LOGS must be a non-negative integer; using 5." >&2
    MAX_FAILURE_LOGS=5
fi

print_failure_log() {
    local name="$1"
    local log_file="$2"
    local max_lines="${GARMIN_BUILD_LOG_PREVIEW_LINES:-240}"

    if ! [[ "$max_lines" =~ ^[0-9]+$ ]]; then
        max_lines=240
    fi

    echo "::group::MonkeyC failure: $name"
    if [[ -s "$log_file" ]]; then
        sed -n "1,${max_lines}p" "$log_file"
        local line_count
        line_count="$(wc -l < "$log_file" | tr -d ' ')"
        if (( line_count > max_lines )); then
            echo "... log preview truncated to ${max_lines} lines. Full log: $log_file"
        fi
    else
        echo "No compiler output captured. Log file: $log_file"
    fi
    echo "::endgroup::"
}

record_failure() {
    local name="$1"
    local log_file="$2"

    FAILED+=("$name")
    if (( ${#FAILED[@]} <= MAX_FAILURE_LOGS )); then
        print_failure_log "$name" "$log_file"
    elif (( ${#FAILED[@]} == MAX_FAILURE_LOGS + 1 )); then
        echo "Additional failure logs suppressed in console. Full logs are in $LOG_DIR"
    fi
}

# --- Find monkeyc -----------------------------------------------------------

find_monkeyc() {
    if [[ -n "${MONKEYC:-}" ]]; then
        if [[ -x "$MONKEYC" ]]; then
            echo "$MONKEYC"
            return
        fi
        echo "Error: MONKEYC is set but not executable: $MONKEYC" >&2
        exit 1
    fi

    if [[ -n "${CONNECTIQ_SDK_HOME:-}" && -x "$CONNECTIQ_SDK_HOME/bin/monkeyc" ]]; then
        echo "$CONNECTIQ_SDK_HOME/bin/monkeyc"
        return
    fi

    local active_sdk_cfg="$HOME/Library/Application Support/Garmin/ConnectIQ/current-sdk.cfg"
    if [[ -f "$active_sdk_cfg" ]]; then
        local active_sdk
        active_sdk="$(cat "$active_sdk_cfg")"
        if [[ -x "$active_sdk/bin/monkeyc" ]]; then
            echo "$active_sdk/bin/monkeyc"
            return
        fi
    fi

    local candidates=(
        "$HOME/.Garmin/ConnectIQ/Sdks"/*/bin/monkeyc
        "$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks"/*/bin/monkeyc
    )
    for c in "${candidates[@]}"; do
        if [[ -x "$c" ]]; then
            echo "$c"
            return
        fi
    done
    echo "Error: monkeyc not found in the Garmin SDK Manager locations." >&2
    echo "Install the Connect IQ SDK with Garmin Connect IQ SDK Manager." >&2
    exit 1
}

# --- Find developer key ------------------------------------------------------

find_key() {
    for path in \
        "${GARMIN_DEV_KEY:-}" \
        "$REPO_ROOT/../developer_key" \
        "$REPO_ROOT/developer_key" \
        "$HOME/.Garmin/developer_key" \
        "$HOME/.Garmin/developer_key.der"; do
        if [[ -n "$path" && -f "$path" ]]; then
            echo "$path"
            return
        fi
    done
    echo "Error: developer key not found." >&2
    echo "Set GARMIN_DEV_KEY=/path/to/key or place it next to the repo." >&2
    exit 1
}

# --- Parse args ---------------------------------------------------------------

MODE="all"
KEY_OVERRIDE=""
TARGET_DEVICE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        release) MODE="release" ;;
        store)   MODE="store" ;;
        all)     MODE="all" ;;
        --key)
            if [[ $# -lt 2 ]]; then
                echo "Error: --key requires a path" >&2
                exit 1
            fi
            KEY_OVERRIDE="$2"
            shift
            ;;
        --device)
            if [[ $# -lt 2 ]]; then
                echo "Error: --device requires a Connect IQ product ID" >&2
                exit 1
            fi
            TARGET_DEVICE="$2"
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [release|store|all] [--device product-id] [--key /path/to/developer_key]"
            echo ""
            echo "  release  Build per-device .prg files (for GitHub releases)"
            echo "  store    Build .iq package (for Connect IQ Store)"
            echo "  all      Both (default)"
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

MONKEYC="$(find_monkeyc)"

if [[ -n "$KEY_OVERRIDE" ]]; then
    DEV_KEY="$KEY_OVERRIDE"
else
    DEV_KEY="$(find_key)"
fi

# --- Extract devices from manifest -------------------------------------------

DEVICES=()
while IFS= read -r line; do
    DEVICES+=("$line")
done < <(sed -nE 's/.*product id="([^"]+)".*/\1/p' "$MANIFEST")

if [[ ${#DEVICES[@]} -eq 0 ]]; then
    echo "Error: no devices found in $MANIFEST" >&2
    exit 1
fi

if [[ -n "$TARGET_DEVICE" ]]; then
    device_found=false
    for device in "${DEVICES[@]}"; do
        if [[ "$device" == "$TARGET_DEVICE" ]]; then
            device_found=true
            break
        fi
    done
    if [[ "$device_found" != true ]]; then
        echo "Error: device '$TARGET_DEVICE' is not declared in $MANIFEST" >&2
        exit 1
    fi
    DEVICES=("$TARGET_DEVICE")
fi

echo "SDK:    $MONKEYC"
echo "Key:    $DEV_KEY"
echo "Devices: ${#DEVICES[@]} (${DEVICES[*]})"
echo ""

mkdir -p "$OUT_DIR"
rm -rf "$LOG_DIR"
mkdir -p "$LOG_DIR"

if [[ "$MODE" == "release" || "$MODE" == "all" ]]; then
    rm -f "$OUT_DIR"/*.prg
fi
if [[ "$MODE" == "store" || "$MODE" == "all" ]]; then
    rm -f "$OUT_DIR"/*.iq
fi

FAILED=()

# --- Build per-device .prg files ---------------------------------------------

if [[ "$MODE" == "release" || "$MODE" == "all" ]]; then
    echo "=== Building per-device .prg files ==="
    for device in "${DEVICES[@]}"; do
        outfile="$OUT_DIR/${APP_NAME}-${device}.prg"
        log_file="$LOG_DIR/${device}.log"
        printf "  %-20s " "$device"
        if {
            echo "monkeyc=$MONKEYC"
            echo "device=$device"
            echo "jungle=$JUNGLE"
            echo "output=$outfile"
            echo ""
            "$MONKEYC" \
                -d "$device" \
                -f "$JUNGLE" \
                -o "$outfile" \
                -y "$DEV_KEY" \
                -r
        } >"$log_file" 2>&1; then
            size=$(stat --printf="%s" "$outfile" 2>/dev/null || stat -f%z "$outfile")
            echo "OK  $(( size / 1024 ))K"
        else
            echo "FAILED"
            record_failure "$device" "$log_file"
            rm -f "$outfile"
        fi
    done
    echo ""
fi

# --- Build .iq store package -------------------------------------------------

if [[ "$MODE" == "store" || "$MODE" == "all" ]]; then
    echo "=== Building .iq store package ==="
    iq_file="$OUT_DIR/${APP_NAME}.iq"
    log_file="$LOG_DIR/iq-package.log"
    if {
        echo "monkeyc=$MONKEYC"
        echo "package=iq"
        echo "jungle=$JUNGLE"
        echo "output=$iq_file"
        echo ""
        "$MONKEYC" \
            -e \
            -f "$JUNGLE" \
            -o "$iq_file" \
            -y "$DEV_KEY" \
            -r
    } >"$log_file" 2>&1; then
        size=$(stat --printf="%s" "$iq_file" 2>/dev/null || stat -f%z "$iq_file")
        echo "  ${APP_NAME}.iq  OK  $(( size / 1024 ))K"
    else
        echo "  ${APP_NAME}.iq  FAILED"
        record_failure "iq-package" "$log_file"
        rm -f "$iq_file"
    fi
    echo ""
fi

# --- Summary ------------------------------------------------------------------

# Clean up intermediate build artifacts
rm -rf "$OUT_DIR"/gen "$OUT_DIR"/internal-mir "$OUT_DIR"/mir
rm -f "$OUT_DIR"/*.debug.xml

echo "=== Output ==="
echo "Compiler logs: $LOG_DIR"
ARTIFACTS=()
for artifact in "$OUT_DIR"/*.prg "$OUT_DIR"/*.iq; do
    if [[ -e "$artifact" ]]; then
        ARTIFACTS+=("$artifact")
    fi
done

if [[ ${#ARTIFACTS[@]} -gt 0 ]]; then
    ls -lh "${ARTIFACTS[@]}"
fi
echo ""

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "WARNING: ${#FAILED[@]} build(s) failed: ${FAILED[*]}"
    exit 1
else
    echo "All builds succeeded."
fi
