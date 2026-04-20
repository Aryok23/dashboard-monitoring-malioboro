import axios from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AlertsPanel from '../components/AlertsPanel'
import CameraSelector from '../components/CameraSelector'
import CrowdChart from '../components/CrowdChart'
import DetectionOverlay from '../components/DetectionOverlay'
import LiveFeed from '../components/LiveFeed'

const API = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws/active'

function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
}

export default function Dashboard() {
  const navigate = useNavigate()

  const [cameras, setCameras] = useState([])
  const [detections, setDetections] = useState([])
  const [counts, setCounts] = useState({})
  const [activeCamera, setActiveCamera] = useState(null)
  const [wsConnected, setWsConnected] = useState(false)

  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  // Keep a ref so the WS message handler always sees the latest cameras list
  const camerasRef = useRef([])

  useEffect(() => {
    axios
      .get(`${API}/cameras`, { headers: authHeaders() })
      .then(({ data }) => {
        setCameras(data)
        camerasRef.current = data
      })
      .catch(console.error)
  }, [])

  const connectWs = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
    }

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setWsConnected(true)
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'detection') {
          setDetections(msg.detections || [])
          setCounts(msg.counts || {})
          // Sync dropdown to whatever camera the backend is actually streaming
          setActiveCamera((prev) => {
            if (prev && prev.id === msg.camera_id) return prev
            const cam = camerasRef.current.find((c) => c.id === msg.camera_id)
            return cam || prev
          })
        }
      } catch {
        // ignore malformed message
      }
    }

    ws.onerror = () => {
      setWsConnected(false)
    }

    ws.onclose = () => {
      setWsConnected(false)
      reconnectTimer.current = setTimeout(connectWs, 3000)
    }
  }, [])

  useEffect(() => {
    connectWs()
    return () => {
      if (wsRef.current) wsRef.current.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [connectWs])

  function handleLogout() {
    localStorage.removeItem('access_token')
    navigate('/login')
  }

  function handleCameraSwitch(camera) {
    setActiveCamera(camera)
    setCounts({})
    setDetections([])
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white flex flex-col">
      {/* Top bar */}
      <header className="bg-gray-900 border-b border-gray-700 px-6 py-3 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center">
            <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M15 10l4.553-2.069A1 1 0 0121 8.82V15a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" />
            </svg>
          </div>
          <span className="font-semibold text-white">Malioboro Monitor</span>
        </div>
        <button
          onClick={handleLogout}
          className="text-sm text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700 px-3 py-1.5 rounded-lg transition-colors"
        >
          Keluar
        </button>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-72 flex-shrink-0 bg-gray-900 border-r border-gray-700 flex flex-col overflow-y-auto">
          <div className="p-4 border-b border-gray-700">
            <CameraSelector cameras={cameras} onSwitch={handleCameraSwitch} activeCamera={activeCamera} />
          </div>
          <div className="flex-1 p-4 overflow-y-auto">
            <AlertsPanel />
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 flex flex-col overflow-y-auto p-4 gap-4">
          <LiveFeed
            camera={activeCamera}
            connected={wsConnected}
          />
          <DetectionOverlay counts={counts} />
          <CrowdChart activeCamera={activeCamera} cameras={cameras} />
        </main>
      </div>
    </div>
  )
}
