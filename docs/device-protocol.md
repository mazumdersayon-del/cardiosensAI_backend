# CardiosensAI device protocol (v1)

How a CardiosensAI 12-lead device (ESP32, Wi-Fi) talks to the platform: provisioning, pairing, 20-second ECG
snapshots, Live (20 s on request), reset. This is the contract the firmware implements. The reference implementation of the
device side is the simulator `../Test_Scripts/stream_device.py`; the server side is `app/services/device_hub.py`
(rules), `app/scripts/mqtt_bridge.py` (broker ↔ database) and `app/core/devicekeys.py` (keys, signatures).

```
 device ──MQTT/TLS 8883──▶ Mosquitto ──127.0.0.1:1883──▶ bridge (cardiosensai-mqtt) ──▶ PostgreSQL + storage
        ◀── retained state, acks ──            ◀── device_outbox rows ── API (pairing, config, Live)
```

## 1. Identity and keys

Each device is made with:

| Item | Where | Notes |
|---|---|---|
| Device ID (`uid`) | printed label + flash | 6–64 characters, `A-Z a-z 0-9 - _`, starts with a letter or digit. It is the MQTT username, the client ID and a topic level. |
| Serial number | printed label | What the user types next to the device ID when adding the device. Never sent by the device. |
| Factory secret `K` | flash only (encrypted flash / eFuse key) | 32 random bytes from a hardware RNG, 64 hex characters in the factory CSV. Never printed, never logged, never sent. |

Derived on the device (and once on the server, at import):

```
mqtt_password = hex( HMAC-SHA256(K, "cardiosens/mqtt/v1") )      # 64 lowercase hex characters
sign_key      =      HMAC-SHA256(K, "cardiosens/sign/v1")         # 32 bytes
```

The server keeps only a Mosquitto PBKDF2 hash of `mqtt_password` and `sign_key` encrypted with AES-256-GCM under
`DEVICE_KEYS_SECRET`. It never keeps `K`.

**Provisioning.** The factory sends a CSV to CardiosensAI staff, who upload it under Admin → Devices:

```
device_uid,serial,model_code,secret,batch,test
CS12-000123,SN12L-000123,cardiosensai-12lead-dev,<64 hex>,2026-11,0
```

The import is all or nothing (one bad row → nothing imported, every error listed). `test=1` marks a test unit:
its data is stored as `acquisition: simulated` and it is refused on servers without test streams (production,
unless `DEVICE_TEST_STREAMS=true`). Delete the CSV after the import: it holds every device's secret.

## 2. Connection

| | Production | Local (`run_local.sh`) |
|---|---|---|
| Host | `platform.cardiosens.ai` (the API domain) | `localhost` (or the PC's address with `--lan`) |
| Port | 8883, TLS 1.2+, server certificate checked against the public CA roots | 1883, plain |
| Username / client ID | `uid` | `uid` |
| Password | `mqtt_password` | `mqtt_password` |
| MQTT | 3.1.1 or 5, clean session false, keepalive 60 s | same |

The firmware must verify the server certificate (ESP-IDF: `esp_crt_bundle_attach`), never skip verification, and
reconnect with backoff (1 s doubling to 60 s, plus jitter).

**Topics** (the broker ACL allows nothing else):

| Topic | Direction | QoS | Retained |
|---|---|---|---|
| `cs/d/{uid}/up` | device → server | 1 | no (retained uplinks are ignored) |
| `cs/d/{uid}/down/state` | server → device | 1 | **yes**: the device always gets the latest state on subscribe |
| `cs/d/{uid}/down/ack` | server → device | 1 | no |

Subscribe to `cs/d/{uid}/down/#` before publishing anything. Maximum message size: 512 KB.

## 3. Message format (both directions)

```
<64 lowercase hex HMAC-SHA256(sign_key, json_bytes)> "." <json_bytes>
```

The signature covers the exact JSON bytes after the dot (no re-serialisation). Every JSON object carries:

| Field | Meaning |
|---|---|
| `v` | `1` |
| `t` | message type |
| `uid` | the device ID (must equal the login) |
| `ts` | Unix time in seconds (must be within ±5 minutes of the server: sync with SNTP before the first message) |
| `mid` | uplinks only: a new random id per message, 16–32 lowercase hex characters (8–16 random bytes). Resending the same `mid` is refused as `replayed`, so a retried message needs a new `mid`. |

The device must check the signature on every downlink with its own `sign_key`, and ignore messages that fail it,
carry another `uid`, or a state `ver` lower than the last one it applied.

## 4. Device → server

| `t` | Fields | When |
|---|---|---|
| `hello` | `fw` (firmware version) | After every connect. The server answers by (re)publishing the state. |
| `status` | `battery` 0–100, `fw` | Optional, e.g. every 15 minutes while paired. |
| `pair_accept` | `pairing` (id from the state) | The user pressed the button while the state is `pairing`. |
| `reset` | — | Long press (10 s): unpair. Also wipe the local snapshot buffer. |
| `snapshot` | see §6 | On schedule while `config.recording` is true. |
| `live` | `seq`, `samples`, `hr` | Once a second while recording a Live request (§7). |
| `live_end` | — | A Live recording stopped before its 20 s (state cleared it). |

## 5. Server → device

**State** (`down/state`, retained, always complete):

```json
{"v":1,"uid":"CS12-000123","ts":1793000000,"t":"state","ver":17,
 "state":"paired",
 "config":{"recording":true,"intervalMin":60,"quietStart":"22:00","quietEnd":"07:00","tz":"Asia/Kolkata",
           "utcOffsetMin":330,"snapshotS":20,"fs":500,"leads":["I","II","III","aVR","aVL","aVF","V1","V2","V3","V4","V5","V6"],
           "uvPerCount":1,"since":1792990000},
 "live":{"requested":1793000240,"until":1793000300,"captureS":20}}
```

| `state` | Device behaviour |
|---|---|
| `unpaired` | Idle; LED shows "not paired". Discard anything buffered. |
| `pairing` | Someone added this device in the app. `pairing: {id, expires}`. Blink the LED until `expires`; on a button press send `pair_accept` with `pairing.id`. |
| `paired` | Follow `config` and `live`. `recording: false` means paired but not writing to any patient (a doctor's device with no patient assigned, or the link ended): don't record. |
| `blocked` | Staff blocked the device. Stop everything; the broker will also refuse the next login. |

Config: `intervalMin` is one of 0 (off), 5, 15, 30, 60, 120, 240, 360, 720, 1440. Quiet hours `quietStart`–`quietEnd`
(HH:MM local time, may cross midnight; both `null` = none) use `utcOffsetMin`, which the server keeps up to date
for daylight saving. Never record before `since`. `live` (§7) asks for one 20 s recording now; `requested` identifies
the request (record once per value), `until` is when it lapses.

**Ack** (`down/ack`) for each uplink that needs one, and for every rejected one:

```json
{"v":1,"uid":"CS12-000123","ts":1793000001,"t":"ack","ref":"<the uplink's mid>","ok":true,"code":null,"seq":42,"quality":"good"}
```

`ok:false` carries `code` and a `message`. `ref` is `null` when the signature or format was bad.

## 6. Snapshots

A snapshot is 20 s of 12-lead ECG: 500 Hz, int16 little-endian, 1 µV per count, frames interleaved in the lead
order above, exactly 10 000 frames = 240 000 bytes, base64 in `samples`.

```json
{"v":1,"t":"snapshot","uid":"CS12-000123","ts":1793000400,"mid":"9f2c…","seq":42,
 "recordedAt":1793000380000,"fs":500,"leads":[…12 names…],"uvPerCount":1,"samples":"<base64>","leadOff":["V5"]}
```

- `seq`: per-device counter, never reused (keep it in flash). Sending the same `seq` again is answered
  `ok:true, duplicate:true`; drop it from the buffer.
- `recordedAt`: start of the recording, Unix **milliseconds**.
- `leadOff`: leads the front end flagged as disconnected.
- III, aVR, aVL and aVF must be computed from the rounded int16 I and II exactly as
  `III = II − I`, `aVR = −(I + II)/2`, `aVL = I − II/2`, `aVF = II − I/2` (±2 counts; frames where a lead is at
  ±32767/−32768 are exempt). If the front end measures them separately, recompute before sending. The server
  refuses snapshots whose derived leads don't match: it is the check that catches corrupted or fabricated data.

**Buffering.** Keep up to 48 snapshots (or 7 days) while offline; upload oldest first, one at a time, and
delete each one only after its ack (`ok:true`, or `ok:false` with a code that can never succeed). Keep the
schedule while offline.

**Server checks** (any failure → `ok:false` + code, nothing stored):

| Code | Cause |
|---|---|
| `bad_signature`, `bad_envelope`, `bad_json`, `too_large` | Wrong key, broken framing, not JSON, > 512 KB |
| `clock_skew` | `ts` more than 5 minutes off |
| `replayed` | `mid` seen before |
| `device_blocked`, `test_unit_refused` | Blocked device; test unit on a server without test streams |
| `not_paired`, `not_recording` | No owner, or no linked patient assigned |
| `bad_format` | not 500 Hz / the 12 standard leads in order / 1 µV per count |
| `bad_snapshot`, `bad_samples` | Bad `seq`, `recordedAt`, `leadOff`, or base64 |
| `future_snapshot`, `too_old`, `before_pairing` | > 5 min in the future, > 7 days old, before pairing/assignment |
| `overlapping` | Another snapshot from this device within 20 s |
| `rate_limited` | More than 120 snapshots in an hour |
| `wrong_length`, `no_signal`, `inconsistent_leads` | Not 240 000 bytes; every lead flat; derived leads don't match |

Stored snapshots are graded `good`, `acceptable` or `poor` (lead-off, flat, saturated or noisy leads; heart rate
found) and kept in the patient's record with the device, firmware, interval and whether the patient had given
research consent at the time (`ecg_snapshots.research_consent`). Doctors with access can label them with
PTB-XL SCP statements (`GET /v1/ecg-snapshots/scp-codes`), so the consented, labelled set can later be exported
in PTB-XL form (500 Hz, 12 leads, µV, `scp_codes`). There is no export yet.

## 7. Live (record 20 s now)

The owner or a linked doctor presses **Live** (`POST /v1/devices/{id}/live`); the state gets
`live: {requested, until, captureS: 20}` (valid 60 s). The device then, once per `requested` value:

1. starts a 20 s recording at once (skip or postpone a scheduled snapshot that would overlap it);
2. while recording, sends each second as it is taken, so the app shows it live:
   ```json
   {"v":1,"t":"live","uid":"…","ts":…,"mid":"…","seq":0,"samples":"<base64, 1 s = 500 frames>","hr":72}
   ```
   `seq` 0…19. Each chunk must already satisfy the derived-lead rule of §6 (refused as `inconsistent_leads`);
   at most 25 s of chunks per request;
3. uploads the whole 20 s as a normal snapshot with `"trigger":"live"` (§6 rules and buffering apply). The server
   stores it in the patient's ECG list, ends the live view and clears `live` from the state. The streamed chunks are
   only a preview and are not stored.

If `live` disappears before the 20 s are up (someone pressed Cancel), stop, send `live_end`, and discard that
recording. A `"trigger":"live"` snapshot without a request in the last minute is refused (`live_not_requested`).

## 8. Pairing, unpairing, blocking

1. Power on → connect → `hello` → state `unpaired`.
2. The user opens Account → Devices → Add a CardiosensAI device, types the device ID and serial number. The
   server checks both (constant time) and publishes state `pairing` (valid 2 minutes).
3. The user presses the device's button → `pair_accept`. The device is now in the user's account; for a patient
   it records into their own record, for a doctor or clinic into the patient they assign (with an active link).
4. Unpair: remove the device in the app, or hold the button 10 s (`reset`). Staff can block a device
   (Admin → Devices): it is unpaired and loses its broker login.

## 9. Firmware checklist

- Keep `K` in encrypted flash / eFuse; enable secure boot and flash encryption in production builds.
- Verify the TLS certificate; never fall back to plain MQTT.
- SNTP before the first message; use a fresh random `mid` for every message.
- Verify the signature and `ver` of every downlink.
- Keep `seq` in flash; buffer at most 48 snapshots; delete only after the ack.
- Compute III, aVR, aVL, aVF from I and II (§6).
- Rate: never more than one snapshot per scheduled slot; back off on `rate_limited`.
