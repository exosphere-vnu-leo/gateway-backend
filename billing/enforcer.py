import logging
import time

from .geofence import GeoStatus

log = logging.getLogger(__name__)


class MockGateway:
    """Gateway mock để test BillingEnforcer mà không cần server thật."""

    def __init__(self, device_db):
        self.device_db = device_db
        self.bandwidth: dict[str, float] = {}
        self.notifications: list[dict] = []
        self.disconnected: set[str] = set()
        self.active_sessions: dict[str, str] = {}
        self._telemetry: dict[str, object] = {}
        self.admin_ws = MockAdminWS()

    async def set_bandwidth(self, router_id: str, factor: float):
        self.bandwidth[router_id] = factor
        log.info("Router %s bandwidth factor → %.2f", router_id, factor)

    async def send_notification(self, router_id: str, payload: dict):
        self.notifications.append({"router_id": router_id, **payload})
        log.info("Notification → %s: %s", router_id, payload["message"])

    async def disconnect(self, router_id: str):
        self.disconnected.add(router_id)
        self.active_sessions.pop(router_id, None)
        log.warning("Router %s DISCONNECTED (geofence violation)", router_id)

    async def log_violation(self, router_id: str, info: dict):
        self.device_db.log_violation(router_id, info)

    def get_plan(self, router_id: str):
        return self.device_db.get_plan(router_id)

    def get_latest_telemetry(self, router_id: str):
        return self._telemetry.get(router_id)

    def set_telemetry(self, router_id: str, telemetry):
        self._telemetry[router_id] = telemetry


class MockAdminWS:
    def __init__(self):
        self.messages: list[dict] = []

    async def broadcast(self, payload: dict):
        self.messages.append(payload)


class BillingEnforcer:
    def __init__(self, gateway: MockGateway):
        self.gateway = gateway
        self.grace_period_s = 300

    async def enforce(
        self,
        router_id: str,
        status: GeoStatus,
        dist_km: float,
        violation_start: float | None,
    ):
        match status:
            case GeoStatus.INSIDE | GeoStatus.ALLOWED:
                await self.gateway.set_bandwidth(router_id, 1.0)

            case GeoStatus.BUFFER:
                plan = self.gateway.get_plan(router_id)
                radius = plan.home_radius_km if plan else 0.5
                await self.gateway.send_notification(router_id, {
                    "type": "WARNING",
                    "message": (
                        f"Bạn cách tọa độ đăng ký {dist_km:.2f} km. "
                        f"Giới hạn: {radius} km."
                    ),
                })

            case GeoStatus.GRACE:
                remaining = self.grace_period_s - (time.time() - violation_start)
                await self.gateway.set_bandwidth(router_id, 0.5)
                await self.gateway.send_notification(router_id, {
                    "type": "THROTTLE",
                    "message": (
                        f"Ngoài vùng phủ. Kết nối sẽ bị ngắt sau {int(remaining)} giây."
                    ),
                })

            case GeoStatus.VIOLATED:
                await self.gateway.disconnect(router_id)
                await self.gateway.log_violation(router_id, {
                    "timestamp": time.time(),
                    "dist_km": dist_km,
                    "reason": "geofence_exceeded",
                })
                await self.gateway.admin_ws.broadcast({
                    "event": "VIOLATION",
                    "router_id": router_id,
                    "dist_km": dist_km,
                })
