import { writable, derived } from 'svelte/store'

export const devicePositions = writable([])
export const violations = writable([])
export const wsConnected = writable(false)

let ws = null

export function connectLiveFeed(gatewayWsUrl) {
  if (ws) ws.close()

  ws = new WebSocket(gatewayWsUrl)

  ws.onopen = () => wsConnected.set(true)
  ws.onclose = () => {
    wsConnected.set(false)
    // Auto-reconnect sau 3 giây
    setTimeout(() => connectLiveFeed(gatewayWsUrl), 3000)
  }

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data)

    if (data.event === 'VIOLATION') {
      violations.update(v => [
        {
          router_id: data.router_id,
          dist_km:   data.dist_km,
          time:      new Date().toISOString()
        },
        ...v
      ].slice(0, 100))
    }

    if (data.router_id && data.status) {
      devicePositions.update(devices => {
        const idx = devices.findIndex(d => d.id === data.router_id)
        const entry = {
          id:         data.router_id,
          pos:        data.position,
          status:     data.status,       // INSIDE / BUFFER / GRACE / VIOLATED / ALLOWED
          dist:       data.dist_km,
          plan:       data.plan_type,
          suspicious: data.suspicious
        }
        if (idx >= 0) devices[idx] = entry
        else devices.push(entry)
        return [...devices]
      })
    }
  }
}

export const activeViolations = derived(
  devicePositions,
  $d => $d.filter(d => d.status === 'VIOLATED').length
)

export const statusColor = {
  INSIDE:   '#1D9E75',
  BUFFER:   '#BA7517',
  GRACE:    '#E24B4A',
  VIOLATED: '#A32D2D',
  ALLOWED:  '#185FA5'
}
