<script>
  import { onMount, onDestroy } from 'svelte'
  import { devicePositions, activeViolations, statusColor, connectLiveFeed } from '../stores/billing.js'

  export let gatewayWsUrl = 'ws://localhost:8000/admin/live-feed'

  let mapEl
  let map
  let markers = {}

  onMount(async () => {
    // Leaflet chỉ chạy trên browser
    const L = (await import('leaflet')).default
    await import('leaflet/dist/leaflet.css')

    map = L.map(mapEl).setView([10.7769, 106.7009], 13)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors'
    }).addTo(map)

    connectLiveFeed(gatewayWsUrl)

    // Cập nhật markers khi store thay đổi
    const unsub = devicePositions.subscribe(devices => {
      devices.forEach(d => {
        const color = statusColor[d.status] ?? '#888'
        const icon = L.divIcon({
          html: `<div style="background:${color};width:14px;height:14px;border-radius:50%;border:2px solid #fff;"></div>`,
          className: '',
          iconSize: [14, 14]
        })

        if (markers[d.id]) {
          markers[d.id].marker.setLatLng(d.pos)
          markers[d.id].marker.setIcon(icon)
          markers[d.id].marker.setPopupContent(popupHtml(d))
          if (d.plan === 'FIXED' && markers[d.id].circle) {
            markers[d.id].circle.setLatLng(d.pos)
          }
        } else {
          const marker = L.marker(d.pos, { icon })
            .addTo(map)
            .bindPopup(popupHtml(d))

          let circle = null
          if (d.plan === 'FIXED') {
            circle = L.circle(d.pos, { radius: 500, color, fillOpacity: 0.1 }).addTo(map)
          }

          markers[d.id] = { marker, circle }
        }
      })
    })

    return () => { unsub(); map.remove() }
  })

  function popupHtml(d) {
    return `
      <b>${d.id}</b><br>
      Trạng thái: <b style="color:${statusColor[d.status]}">${d.status}</b><br>
      Gói: ${d.plan} &nbsp; Cách nhà: ${d.dist} km<br>
      ${d.suspicious ? '⚠️ Nghi ngờ GPS spoofing' : ''}
    `
  }
</script>

<div class="map-container">
  {#if $activeViolations > 0}
    <div class="alert-banner">
      ⚠️ {$activeViolations} thiết bị đang vi phạm geofence
    </div>
  {/if}

  <div bind:this={mapEl} class="map" />
</div>

<style>
  .map-container { position: relative; width: 100%; height: 100%; }
  .map { width: 100%; height: 100%; min-height: 500px; }
  .alert-banner {
    position: absolute; top: 8px; left: 50%; transform: translateX(-50%);
    z-index: 1000; background: #A32D2D; color: #fff;
    padding: 6px 18px; border-radius: 6px; font-weight: 600;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }
</style>
