from dataclasses import dataclass, field


@dataclass
class DeviceRecord:
    device_id: str
    hardware_serial: str
    plan_type: str = "FIXED"
    home_lat: float = 0.0
    home_lon: float = 0.0
    home_radius_km: float = 0.5


class DeviceDatabase:
    def __init__(self):
        self._devices: dict[str, DeviceRecord] = {}
        self._violations: list[dict] = []

    def add(self, device_id: str, hardware_serial: str):
        self._devices[device_id] = DeviceRecord(device_id=device_id, hardware_serial=hardware_serial)

    def exists(self, device_id: str) -> bool:
        return device_id in self._devices

    def get(self, device_id: str) -> DeviceRecord | None:
        return self._devices.get(device_id)

    def get_plan(self, device_id: str) -> DeviceRecord | None:
        return self._devices.get(device_id)

    def update_plan(self, device_id: str, plan_type: str):
        if device_id in self._devices:
            self._devices[device_id].plan_type = plan_type

    def set_home(self, device_id: str, lat: float, lon: float, radius_km: float = 0.5):
        if device_id in self._devices:
            rec = self._devices[device_id]
            rec.home_lat = lat
            rec.home_lon = lon
            rec.home_radius_km = radius_km

    def log_violation(self, device_id: str, info: dict):
        self._violations.append({"device_id": device_id, **info})

    def get_violations(self, limit: int = 50) -> list[dict]:
        return self._violations[-limit:]

    def all_devices(self) -> list[str]:
        return list(self._devices.keys())
