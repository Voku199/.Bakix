import json
import logging
import os
import threading

from app.database.connection import get_connection

log = logging.getLogger(__name__)

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_MAILTO      = os.getenv("VAPID_MAILTO", "mailto:admin@example.com")


class PushNotificationService:
    """Send web push notifications without needing a Flask request context."""

    def send_to_user(
        self,
        user_id: str,
        title: str,
        body: str,
        *,
        url: str = "/",
        tag: str = "bakix",
    ) -> int:
        """Send to all subscriptions for user_id. Returns number actually sent."""
        if not VAPID_PRIVATE_KEY:
            log.warning("push_service: VAPID_PRIVATE_KEY not configured")
            return 0

        with get_connection() as db:
            rows = db.execute(
                "SELECT endpoint, keys_auth, keys_p256dh "
                "FROM push_subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchall()

        if not rows:
            return 0

        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            log.error("push_service: pywebpush not installed")
            return 0

        payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
        sent = 0
        for row in rows:
            sub_info = {
                "endpoint": row["endpoint"],
                "keys": {
                    "auth":   row["keys_auth"],
                    "p256dh": row["keys_p256dh"],
                },
            }
            try:
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_MAILTO},
                    content_encoding="aes128gcm",
                    ttl=86400,
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None) if exc.response else None
                log.warning("push_service: failed endpoint=%.50s status=%s", row["endpoint"], status)
                if status in (404, 410):
                    self._delete_subscription(row["endpoint"])

        log.info("push_service: user=%.8s sent=%d/%d", user_id, sent, len(rows))
        return sent

    def send_to_user_async(
        self,
        user_id: str,
        title: str,
        body: str,
        *,
        url: str = "/",
        tag: str = "bakix",
    ) -> None:
        """Non-blocking send — dispatches to a daemon thread."""
        threading.Thread(
            target=self.send_to_user,
            args=(user_id, title, body),
            kwargs={"url": url, "tag": tag},
            daemon=True,
        ).start()

    def send_to_all_users(self, title: str, body: str) -> None:
        """Send to every user that has at least one active subscription."""
        with get_connection() as db:
            user_ids = [
                r[0] for r in db.execute(
                    "SELECT DISTINCT user_id FROM push_subscriptions"
                ).fetchall()
            ]
        for uid in user_ids:
            self.send_to_user(uid, title, body)

    def _delete_subscription(self, endpoint: str) -> None:
        with get_connection() as db:
            db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        log.info("push_service: deleted endpoint=%.50s", endpoint)
