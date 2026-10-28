"""The device bridge: carries messages between the MQTT broker (Mosquitto) and the database.

    python -m app.scripts.mqtt_bridge

Subscribes to every device's uplink (`cs/d/+/up`), hands each message to `services.device_hub.handle_uplink`
(signature, replay, pairing and data checks happen there), and publishes what the API and the hub queued in
`device_outbox` (retained state, acks). Every few seconds it expires pairings and live-ECG requests; on start and
every few minutes it rewrites the broker's password file so blocked devices lose their login.

On the server `deploy.sh` runs it as the `cardiosensai-mqtt` service (one instance: the outbox has one publisher).
Locally `run_local.sh` starts it next to a Mosquitto container. Settings: MQTT_HOST/PORT/TLS/USERNAME/PASSWORD.
"""

import logging
import signal
import ssl
import threading
import time

import paho.mqtt.client as mqtt
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.models import DeviceOutbox
from app.services import device_hub

log = logging.getLogger("cardiosensai.bridge")
UPLINKS = "cs/d/+/up"
HOUSEKEEPING_S = 5
LOGINS_S = 300


class Bridge:
    def __init__(self) -> None:
        s = get_settings()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cardiosensai-bridge", clean_session=False)
        self.client.username_pw_set(s.mqtt_username, s.mqtt_password or None)
        if s.mqtt_tls:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 30)

    def _on_connect(self, client, userdata, flags, reason_code, properties):  # noqa: ANN001
        if reason_code.is_failure:
            log.error("broker refused the bridge: %s", reason_code)
            return
        client.subscribe(UPLINKS, qos=1)
        self.connected.set()
        self.wake.set()
        log.info("connected to the broker, subscribed to %s", UPLINKS)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):  # noqa: ANN001
        self.connected.clear()
        log.warning("disconnected from the broker: %s", reason_code)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:  # noqa: ANN001
        parts = msg.topic.split("/")
        if len(parts) != 4 or parts[:2] != ["cs", "d"] or parts[3] != "up" or msg.retain:
            return  # the broker ACL only lets device X publish on cs/d/X/up; anything else is ignored
        try:
            with SessionLocal() as db:
                device_hub.handle_uplink(db, parts[2], msg.payload)
        except Exception:
            log.exception("failed to handle a message from %s", parts[2][:64])
        self.wake.set()

    def publish_outbox(self) -> int:
        if not self.connected.is_set():
            return 0
        sent = 0
        with SessionLocal() as db:
            rows = list(db.scalars(select(DeviceOutbox).where(DeviceOutbox.sent_at.is_(None)).order_by(DeviceOutbox.id).limit(500)))
            for row in rows:
                info = self.client.publish(row.topic, row.payload.encode(), qos=1, retain=row.retain)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    break
                row.sent_at = utcnow()
                sent += 1
            db.commit()
        return sent

    def run(self) -> None:
        s = get_settings()
        with SessionLocal() as db:
            path = device_hub.sync_broker_logins(db)
        log.info("broker logins written to %s", path)
        self.client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=60)
        self.client.loop_start()
        last_house = last_logins = 0.0
        try:
            while not self.stop.is_set():
                self.wake.wait(1.0)
                self.wake.clear()
                try:
                    self.publish_outbox()
                    now = time.monotonic()
                    if now - last_house >= HOUSEKEEPING_S:
                        with SessionLocal() as db:
                            device_hub.housekeeping(db)
                        last_house = now
                    if now - last_logins >= LOGINS_S:
                        with SessionLocal() as db:
                            device_hub.sync_broker_logins(db)
                        last_logins = now
                except Exception:
                    log.exception("bridge loop error")
                    time.sleep(2)
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bridge = Bridge()
    signal.signal(signal.SIGTERM, lambda *_: bridge.stop.set())
    signal.signal(signal.SIGINT, lambda *_: bridge.stop.set())
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
