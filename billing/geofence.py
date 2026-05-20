import time
from dataclasses import dataclass, field
from enum import Enum

from .geolocation import haversine


class GeoStatus(Enum):
    INSIDE = "INSIDE"
    BUFFER = "BUFFER"
    GRACE = "GRACE"
    VIOLATED = "VIOLATED"
    ALLOWED = "ALLOWED"    # Gói Mobility — không giới hạn vị trí


@dataclass
class GeofenceConfig:
    allowed_radius_km: float = 0.5
    buffer_zone_km: float = 0.3      # vùng đệm: 0.5 → 0.8 km
    grace_period_s: int = 300        # 5 phút trước khi ngắt


class GeofenceMonitor:
    def __init__(self, config: GeofenceConfig = None):
        self.config = config or GeofenceConfig()
        self.violation_starts: dict[str, float] = {}   # {router_id: timestamp}

    def check(
        self,
        router_id: str,
        plan_type: str,
        current_pos: tuple[float, float],
        home_pos: tuple[float, float],
    ) -> GeoStatus:

        if plan_type == "MOBILITY":
            return GeoStatus.ALLOWED

        dist = haversine(*current_pos, *home_pos)
        r = self.config.allowed_radius_km
        b = r + self.config.buffer_zone_km

        if dist <= r:
            self.violation_starts.pop(router_id, None)
            return GeoStatus.INSIDE

        if dist <= b:
            return GeoStatus.BUFFER

        # Ngoài buffer — tính grace period
        now = time.time()
        if router_id not in self.violation_starts:
            self.violation_starts[router_id] = now

        elapsed = now - self.violation_starts[router_id]
        if elapsed < self.config.grace_period_s:
            return GeoStatus.GRACE

        return GeoStatus.VIOLATED
