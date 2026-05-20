import math
import logging

log = logging.getLogger(__name__)

GPS_SPOOF_THRESHOLD_KM = 50.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Khoảng cách km giữa 2 tọa độ địa lý."""
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def triangulate_router_position(
    sat_lat_deg: float,
    sat_lon_deg: float,
    elevation_deg: float,
    azimuth_deg: float,
    distance_km: float,
) -> tuple[float, float]:
    """
    Tính tọa độ Router từ vị trí vệ tinh + el/az/distance.
    Trả về (lat, lon) của Router mặt đất.

    sat_lat_deg, sat_lon_deg: vị trí vệ tinh (đã resolve bằng Skyfield bên ngoài)
    elevation_deg: góc ngẩng từ Router lên vệ tinh
    azimuth_deg: phương vị từ Router tới vệ tinh (0=Bắc, 90=Đông)
    distance_km: khoảng cách slant range vệ tinh-Router
    """
    el_rad = math.radians(elevation_deg)
    az_rad = math.radians(azimuth_deg)

    # Hình chiếu khoảng cách xuống mặt đất
    ground_dist = distance_km * math.cos(el_rad)

    # Offset kinh/vĩ độ (spherical approximation)
    dlat = (ground_dist * math.cos(az_rad)) / 111.32
    dlon = (ground_dist * math.sin(az_rad)) / (111.32 * math.cos(math.radians(sat_lat_deg)))

    router_lat = sat_lat_deg - dlat
    router_lon = sat_lon_deg - dlon
    return (router_lat, router_lon)


def validate_position(
    router_id: str,
    reported_gps: tuple[float, float] | None,
    derived_pos: tuple[float, float],
) -> tuple[tuple[float, float], bool]:
    """
    Trả về (position, is_suspicious).
    Nếu GPS-reported và satellite-derived sai nhau > 50km → nghi ngờ spoofing.
    """
    if reported_gps is None:
        return derived_pos, False

    r_lat, r_lon = reported_gps
    d_lat, d_lon = derived_pos
    discrepancy = haversine(r_lat, r_lon, d_lat, d_lon)

    if discrepancy > GPS_SPOOF_THRESHOLD_KM:
        log.warning(
            "Router %s GPS mismatch: %.1f km (reported vs satellite-derived)",
            router_id,
            discrepancy,
        )
        # Tin vào satellite-derived, không tin GPS báo cáo
        return derived_pos, True

    # GPS hợp lệ, dùng GPS vì chính xác hơn
    return reported_gps, False
