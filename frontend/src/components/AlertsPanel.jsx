import axios from 'axios'
import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'

function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
}

function AlertBadge({ type }) {
  if (type === 'HIGH_CROWD') {
    return (
      <span className="text-xs font-bold bg-red-900/60 text-red-300 border border-red-700 px-1.5 py-0.5 rounded-full">
        PADAT
      </span>
    )
  }
  return (
    <span className="text-xs font-bold bg-orange-900/60 text-orange-300 border border-orange-700 px-1.5 py-0.5 rounded-full">
      LALU LINTAS
    </span>
  )
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString('id-ID', {
      day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function AlertsPanel() {
  const [alerts, setAlerts] = useState([])

  async function fetchAlerts() {
    try {
      const { data } = await axios.get(`${API}/alerts?limit=50`, { headers: authHeaders() })
      setAlerts(data)
    } catch (err) {
      console.error('Failed to fetch alerts:', err)
    }
  }

  useEffect(() => {
    fetchAlerts()
    const interval = setInterval(fetchAlerts, 30000)
    return () => clearInterval(interval)
  }, [])

  async function handleDismiss(id) {
    try {
      await axios.post(`${API}/alerts/${id}/read`, {}, { headers: authHeaders() })
      setAlerts((prev) => prev.filter((a) => a.id !== id))
    } catch (err) {
      console.error('Failed to mark alert:', err)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Peringatan Kamera
        </h3>
        {alerts.length > 0 && (
          <span className="text-xs bg-red-600 text-white rounded-full px-1.5 py-0.5 font-bold">
            {alerts.length}
          </span>
        )}
      </div>

      {alerts.length === 0 ? (
        <div className="text-center py-8">
          <svg className="w-8 h-8 text-gray-700 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-gray-600 text-xs">Tidak ada peringatan aktif</p>
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map((alert) => (
            <button
              key={alert.id}
              onClick={() => handleDismiss(alert.id)}
              className="w-full text-left bg-gray-800 hover:bg-gray-750 border border-gray-700
                         rounded-xl p-3 transition-colors group"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-white truncate">
                    {alert.camera_name || `Kamera ${alert.camera_id}`}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">{formatTime(alert.timestamp)}</p>
                  {alert.description && (
                    <p className="text-xs text-gray-400 mt-1 line-clamp-2">{alert.description}</p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <AlertBadge type={alert.alert_type} />
                  <span className="text-gray-600 group-hover:text-gray-400 text-xs transition-colors">
                    Tutup
                  </span>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
