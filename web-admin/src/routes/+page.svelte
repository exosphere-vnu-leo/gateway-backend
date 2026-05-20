<script>
  import GeofenceMap from '../components/GeofenceMap.svelte'
  import { devicePositions, violations, wsConnected } from '../stores/billing.js'
  import { revokeDevice, updatePlan } from '../stores/auth.js'

  const statusLabel = {
    INSIDE:   'Trong vùng',
    BUFFER:   'Vùng đệm',
    GRACE:    'Grace period',
    VIOLATED: 'Vi phạm',
    ALLOWED:  'Di động'
  }

  async function handleRevoke(id) {
    if (!confirm(`Thu hồi thiết bị ${id}?`)) return
    try {
      await revokeDevice(id)
      alert(`Đã revoke ${id}`)
    } catch (e) {
      alert(`Lỗi: ${e.message}`)
    }
  }

  async function handlePlanToggle(device) {
    const newPlan = device.plan === 'FIXED' ? 'MOBILITY' : 'FIXED'
    try {
      await updatePlan(device.id, newPlan)
    } catch (e) {
      alert(`Lỗi đổi gói: ${e.message}`)
    }
  }
</script>

<main>
  <header>
    <h1>VNU-LEO Admin Dashboard</h1>
    <span class="ws-status" class:connected={$wsConnected}>
      {$wsConnected ? '● Live' : '○ Offline'}
    </span>
  </header>

  <div class="layout">
    <section class="map-section">
      <GeofenceMap gatewayWsUrl="ws://localhost:8000/admin/live-feed" />
    </section>

    <aside>
      <h2>Thiết bị ({$devicePositions.length})</h2>
      <table>
        <thead>
          <tr><th>ID</th><th>Trạng thái</th><th>Cách nhà</th><th>Gói</th><th>Hành động</th></tr>
        </thead>
        <tbody>
          {#each $devicePositions as d}
            <tr class="status-{d.status.toLowerCase()}">
              <td>{d.id} {d.suspicious ? '⚠️' : ''}</td>
              <td>{statusLabel[d.status]}</td>
              <td>{d.dist} km</td>
              <td>{d.plan}</td>
              <td>
                <button on:click={() => handlePlanToggle(d)}>
                  → {d.plan === 'FIXED' ? 'Mobility' : 'Fixed'}
                </button>
                <button class="danger" on:click={() => handleRevoke(d.id)}>Revoke</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>

      <h2>Vi phạm gần đây</h2>
      <ul class="violations">
        {#each $violations.slice(0, 10) as v}
          <li><b>{v.router_id}</b> — {v.dist_km} km — {v.time}</li>
        {/each}
      </ul>
    </aside>
  </div>
</main>

<style>
  :global(body) { margin: 0; font-family: system-ui, sans-serif; background: #0f1117; color: #e0e0e0; }
  header { display: flex; align-items: center; gap: 12px; padding: 12px 20px; background: #1a1d27; }
  h1 { margin: 0; font-size: 1.2rem; }
  .ws-status { font-size: 0.85rem; color: #888; }
  .ws-status.connected { color: #1D9E75; }
  .layout { display: grid; grid-template-columns: 1fr 420px; height: calc(100vh - 52px); }
  .map-section { height: 100%; }
  aside { overflow-y: auto; padding: 12px; background: #13161f; border-left: 1px solid #2a2d3a; }
  h2 { font-size: 0.9rem; color: #888; margin: 16px 0 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { padding: 4px 6px; text-align: left; border-bottom: 1px solid #2a2d3a; }
  tr.status-violated { background: rgba(163,45,45,0.15); }
  tr.status-grace { background: rgba(226,75,74,0.10); }
  button { font-size: 0.75rem; padding: 2px 6px; margin-right: 4px; cursor: pointer;
           background: #2a2d3a; color: #e0e0e0; border: 1px solid #444; border-radius: 3px; }
  button.danger { color: #E24B4A; border-color: #E24B4A; }
  .violations { list-style: none; padding: 0; margin: 0; font-size: 0.8rem; }
  .violations li { padding: 4px 0; border-bottom: 1px solid #2a2d3a; }
</style>
