import asyncio
import logging
from dataclasses import dataclass

from .enforcer import BillingEnforcer, MockGateway
from .geofence import GeofenceConfig, GeofenceMonitor
from .geolocation import haversine, triangulate_router_position, validate_position

log = logging.getLogger(__name__)


@dataclass
class RouterTelemetry:
    sat_id: str
    sat_lat: float
    sat_lon: float
    elevation: float        # degrees
    azimuth: float          # degrees
    distance_km: float
    timestamp: object       # datetime
    gps: tuple[float, float] | None = None


class SpatioTemporalBillingService:
    def __init__(
        self,
        gateway: MockGateway,
        geo_config: GeofenceConfig = None,
    ):
        self.gateway = gateway
        self.monitor = GeofenceMonitor(geo_config or GeofenceConfig())
        self.enforcer = BillingEnforcer(gateway)

    async def run(self):
        """Chạy liên tục — tick mỗi giây."""
        while True:
            for router_id in list(self.gateway.active_sessions.values()):
                try:
                    await self._process_router(router_id)
                except Exception as e:
                    log.error("Billing error for %s: %s", router_id, e)
            await asyncio.sleep(1)

    async def _process_router(self, router_id: str):
        telemetry: RouterTelemetry = self.gateway.get_latest_telemetry(router_id)
        if telemetry is None:
            return

        plan = self.gateway.get_plan(router_id)
        if plan is None:
            return

        # 1. Triangulate vị trí từ satellite telemetry
        derived_pos = triangulate_router_position(
            telemetry.sat_lat,
            telemetry.sat_lon,
            telemetry.elevation,
            telemetry.azimuth,
            telemetry.distance_km,
        )

        # 2. Validate / anti-spoof
        position, is_suspicious = validate_position(router_id, telemetry.gps, derived_pos)

        # 3. Check geofence
        home_pos = (plan.home_lat, plan.home_lon)
        status = self.monitor.check(router_id, plan.plan_type, position, home_pos)

        # 4. Enforce
        dist = haversine(*position, *home_pos)
        await self.enforcer.enforce(
            router_id,
            status,
            dist,
            self.monitor.violation_starts.get(router_id),
        )

        # 5. Push realtime data lên SvelteKit
        await self.gateway.admin_ws.broadcast({
            "router_id": router_id,
            "position": position,
            "status": status.value,
            "dist_km": round(dist, 3),
            "plan_type": plan.plan_type,
            "suspicious": is_suspicious,
        })
