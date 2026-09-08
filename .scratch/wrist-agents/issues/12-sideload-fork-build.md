# Sideload the fork build onto the watch

Type: task
Status: resolved
Blocked by: none

## Question

Get the fork's watch app onto the epix Pro so the two-provider usage payload renders, given that the store build is installed under the same app id and is invisible over MTP.

## Answer

Resolved 2026-09-07.

- Decision: the fork gets its own app id (`b00505e81cf242fd815ebcfa204adfba` in `garmin/manifest.xml`) instead of sharing upstream's. Reason: the store copy cannot be deleted over MTP (Garmin hides installed PRGs; only `OUT.BIN` shows under `GARMIN/Apps`), and a distinct id keeps the store from overwriting the sideloaded build on a later sync (MODERATE confidence on that behaviour). Consequence: the store copy must be removed by hand in the Connect IQ phone app, and the daemon does not care which id the watch app carries.
- Sideload path: `tools/build_garmin.sh release` (builds every manifest device, about 4 minutes; SDK 9.2.0 via `current-sdk.cfg`, key at `~/.Garmin/developer_key.der`), then `python3 tools/sideload_mtp.py build/garmin/oh-my-wrist-epix2pro51mm.prg --name oh-my-wrist-fork.prg`. Plain `mtp-sendfile` fails on this device because it never sets the storage id; the script sets storage `0x00020001` and folder `16777274` explicitly through libmtp's C API.
- The watch enumerates as MTP (vendor 0x091E, product 0x50DA) on USB; nothing mounts as a volume, which is expected. OpenMTP is installed but was not needed.
- Verified: `mtp-files` lists `oh-my-wrist-fork.prg`, 44,604 bytes, in the Apps folder. On-watch install happens when the watch leaves USB mode; the on-wrist check is pending Nelson.
