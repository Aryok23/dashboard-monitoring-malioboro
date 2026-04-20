import axios from 'axios'
import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'

function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
}

export default function CameraSelector({ cameras, onSwitch, activeCamera }) {
  const [selectedId, setSelectedId] = useState(null)
  const [switching, setSwitching] = useState(false)

  // Sync dropdown whenever activeCamera changes (e.g. synced from WS frame)
  useEffect(() => {
    if (activeCamera) setSelectedId(activeCamera.id)
  }, [activeCamera])

  async function handleChange(e) {
    const id = Number(e.target.value)
    setSelectedId(id)
    setSwitching(true)
    try {
      await axios.post(`${API}/camera/switch/${id}`, {}, { headers: authHeaders() })
      const camera = cameras.find((c) => c.id === id)
      if (camera) onSwitch(camera)
    } catch (err) {
      console.error('Camera switch failed:', err)
    } finally {
      setSwitching(false)
    }
  }

  // Group cameras by zone
  const zones = {}
  for (const cam of cameras) {
    if (!zones[cam.zone]) zones[cam.zone] = []
    zones[cam.zone].push(cam)
  }

  return (
    <div>
      <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
        Kamera Aktif
      </label>
      <select
        value={selectedId ?? ''}
        onChange={handleChange}
        disabled={switching || cameras.length === 0}
        className="w-full bg-gray-800 border border-gray-600 text-white text-sm rounded-lg px-3 py-2
                   focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-60"
      >
        {Object.entries(zones).map(([zone, cams]) => (
          <optgroup key={zone} label={`— ${zone} —`} className="text-gray-400 bg-gray-800">
            {cams.map((cam) => (
              <option key={cam.id} value={cam.id} className="bg-gray-800 text-white">
                {cam.name}
                {cam.is_ptz ? ' (PTZ)' : ''}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {switching && (
        <p className="text-xs text-blue-400 mt-1">Mengganti kamera...</p>
      )}
    </div>
  )
}
