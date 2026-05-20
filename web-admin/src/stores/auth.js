import { writable } from 'svelte/store'

export const devices = writable([])
export const provisionStatus = writable(null)

const API = import.meta.env.VITE_GATEWAY_URL ?? 'http://localhost:8000'

export async function fetchDevices() {
  // Gateway không có /devices list endpoint — derive từ violations + positions
}

export async function revokeDevice(deviceId) {
  const res = await fetch(`${API}/provision/revoke/${deviceId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function updatePlan(deviceId, planType) {
  const res = await fetch(`${API}/billing/plan/${deviceId}/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_type: planType })
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function setHomeCoords(deviceId, lat, lon, radiusKm = 0.5) {
  const res = await fetch(`${API}/billing/geofence/${deviceId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lon, radius_km: radiusKm })
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getViolations(limit = 50) {
  const res = await fetch(`${API}/billing/violations?limit=${limit}`)
  return res.json()
}
