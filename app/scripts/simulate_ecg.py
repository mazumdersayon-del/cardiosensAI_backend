"""Stream a synthetic 12-lead ECG to a running API as a live recording — for demos and testing the live view.

    python -m app.scripts.simulate_ecg                       # demo patient, 60 s, http://localhost:8000
    python -m app.scripts.simulate_ecg --seconds 0           # until Ctrl+C
    python -m app.scripts.simulate_ecg --base-url http://localhost:8010 --email patient@demo.cardiosens.ai

Signs in as the patient through the public API (email + password), picks their ECG-capable device,
starts a live session, sends one chunk per second, and stops (the session then becomes a recording).
The signal is synthetic (app/services/ecg_synth.py). Standard library only (urllib).
"""

import argparse
import base64
import json
import signal
import sys
import time
import urllib.error
import urllib.request

from app.services.ecg_synth import Synth12


def call(base: str, method: str, path: str, token: str | None = None, body: dict | None = None):
    if not base.startswith(("http://", "https://")):
        raise SystemExit("--base-url must start with http:// or https://")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, method=method, data=data)  # noqa: S310 - scheme checked above
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - URL comes from the operator
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')}") from e


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-url", default="http://localhost:8000", help="API to stream to (default http://localhost:8000)")
    p.add_argument("--email", default="patient@demo.cardiosens.ai", help="patient to sign in as (default the demo patient)")
    p.add_argument("--password", default="cardiosens-demo-2026", help="that patient's password (default the demo password)")
    p.add_argument("--seconds", type=int, default=60, help="length in seconds (default 60; 0 = until Ctrl+C or kill)")
    p.add_argument("--hr", type=int, default=72, help="heart rate of the synthetic signal in bpm (default 72)")
    p.add_argument("--fs", type=int, default=500, help="sample rate in Hz (default 500)")
    args = p.parse_args()

    login = call(args.base_url, "POST", "/v1/auth/login", body={"email": args.email, "password": args.password})
    if login.get("mfaRequired"):
        raise SystemExit("This account uses two-step sign-in; use a patient account without it")
    token = login["accessToken"]
    devices = [d for d in call(args.base_url, "GET", "/v1/devices", token) if "ecg" in d["capabilities"]]
    if not devices:
        raise SystemExit("The patient has no ECG-capable device registered")
    synth = Synth12(fs=args.fs, hr=args.hr, seed=int(time.time()))
    sess = call(
        args.base_url, "POST", "/v1/ecg-live", token, {"deviceId": devices[0]["id"], "leads": list(synth.leads), "sampleRateHz": args.fs}
    )
    print(f"Live ECG {sess['id']} started for {args.email} ({len(synth.leads)} leads, {args.fs} Hz). Ctrl+C to stop.", flush=True)

    # Ctrl+C or `kill` both end the recording cleanly (so it is saved), even when run in the background.
    def _stop(*_):  # noqa: ANN002
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    seq, t0 = 0, time.monotonic()
    try:
        while args.seconds == 0 or seq < args.seconds:
            raw = synth.raw(args.fs)  # one second
            call(
                args.base_url,
                "POST",
                f"/v1/ecg-live/{sess['id']}/chunks",
                token,
                {"seq": seq, "samplesBase64": base64.b64encode(raw).decode(), "averageHr": args.hr},
            )
            seq += 1
            print(f"\r  sent {seq} s", end="", flush=True)
            time.sleep(max(0.0, t0 + seq - time.monotonic()))
    except KeyboardInterrupt:
        pass
    out = call(args.base_url, "POST", f"/v1/ecg-live/{sess['id']}/stop", token, {"quality": "good"})
    print(f"\nStopped after {out['durationS']} s; saved as recording {out['recordingId']}.")


if __name__ == "__main__":
    sys.exit(main())
