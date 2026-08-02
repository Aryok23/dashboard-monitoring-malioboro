import axios from 'axios'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { formatWib, wibDateStr } from '../utils/wib'

const API = import.meta.env.VITE_API_URL
const LIMIT = 20

// Same palette as LiveFeed.jsx's bbox overlay, for visual consistency.
const CLASS_COLORS = {
  orang: '#3b82f6',
  sepeda: '#22c55e',
  motor: '#eab308',
  mobil: '#f97316',
  bus: '#ef4444',
  truk: '#a855f7',
  bajaj: '#06b6d4',
  becak: '#ec4899',
  andong: '#84cc16',
}

function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
}

function AlertTypeBadge({ type }) {
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

// Same object-fit:contain scaling math as LiveFeed.jsx's drawDetections(),
// adapted for a static <img> (uses naturalWidth/naturalHeight as the source
// frame size instead of a separate frameSize prop from the WS payload).
function drawBoxes(canvas, img, detections) {
  const ctx = canvas.getContext('2d')
  const elemW = img.clientWidth
  const elemH = img.clientHeight
  canvas.width = elemW
  canvas.height = elemH
  ctx.clearRect(0, 0, elemW, elemH)
  if (!detections || detections.length === 0) return

  const srcW = img.naturalWidth || elemW
  const srcH = img.naturalHeight || elemH
  const videoAspect = srcW / srcH
  const elemAspect = elemW / elemH

  let drawW, drawH, offsetX, offsetY
  if (videoAspect > elemAspect) {
    drawW = elemW
    drawH = elemW / videoAspect
    offsetX = 0
    offsetY = (elemH - drawH) / 2
  } else {
    drawW = elemH * videoAspect
    drawH = elemH
    offsetX = (elemW - drawW) / 2
    offsetY = 0
  }

  const scaleX = drawW / srcW
  const scaleY = drawH / srcH

  for (const det of detections) {
    const [x1, y1, x2, y2] = det.bbox
    const rx = offsetX + x1 * scaleX
    const ry = offsetY + y1 * scaleY
    const rw = (x2 - x1) * scaleX
    const rh = (y2 - y1) * scaleY
    const color = CLASS_COLORS[det.class_name] || '#ffffff'

    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.strokeRect(rx, ry, rw, rh)

    const label = `${det.class_name} ${Math.round((det.confidence ?? 0) * 100)}%`
    ctx.font = 'bold 11px sans-serif'
    const textW = ctx.measureText(label).width + 8
    ctx.globalAlpha = 0.8
    ctx.fillStyle = color
    ctx.fillRect(rx, ry - 20, textW, 20)
    ctx.globalAlpha = 1
    ctx.fillStyle = '#ffffff'
    ctx.fillText(label, rx + 4, ry - 5)
  }
}

function AlertDetail({ alertId, onClose }) {
  const [detail, setDetail] = useState(null)
  const [imageUrl, setImageUrl] = useState(null)
  const [imgLoaded, setImgLoaded] = useState(false)
  const imgRef = useRef(null)
  const canvasRef = useRef(null)

  useEffect(() => {
    let objectUrl = null
    setDetail(null)
    setImageUrl(null)
    setImgLoaded(false)

    axios
      .get(`${API}/alerts/${alertId}`, { headers: authHeaders() })
      .then(({ data }) => setDetail(data))
      .catch((err) => console.error('Failed to fetch alert detail:', err))

    // Image endpoint requires the Authorization header, so it can't be used
    // directly as an <img src>; fetch as a blob and use an object URL.
    axios
      .get(`${API}/alerts/${alertId}/image`, { headers: authHeaders(), responseType: 'blob' })
      .then(({ data }) => {
        objectUrl = URL.createObjectURL(data)
        setImageUrl(objectUrl)
      })
      .catch(() => setImageUrl(null))

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [alertId])

  useEffect(() => {
    const canvas = canvasRef.current
    const img = imgRef.current
    if (!canvas || !img || !imgLoaded || !detail) return
    drawBoxes(canvas, img, detail.detections)
  }, [imgLoaded, detail])

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white">Detail Peringatan</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-sm">
            Tutup
          </button>
        </div>

        {detail && (
          <div className="text-xs text-gray-300 mb-3 space-y-1">
            <p><span className="text-gray-500">Kamera:</span> {detail.camera_name || `Kamera ${detail.camera_id}`}</p>
            <p><span className="text-gray-500">Waktu (WIB):</span> {formatWib(detail.timestamp)}</p>
            <p><span className="text-gray-500">Jenis:</span> <AlertTypeBadge type={detail.alert_type} /></p>
            <p><span className="text-gray-500">Nilai pemicu:</span> {detail.trigger_value}</p>
            {detail.description && (
              <p><span className="text-gray-500">Deskripsi:</span> {detail.description}</p>
            )}
          </div>
        )}

        <div className="relative bg-gray-950 rounded-lg overflow-hidden border border-gray-800">
          {imageUrl ? (
            <>
              <img
                ref={imgRef}
                src={imageUrl}
                alt="Bukti citra peringatan"
                className="w-full max-h-[60vh] object-contain"
                onLoad={() => setImgLoaded(true)}
              />
              <canvas
                ref={canvasRef}
                className="absolute inset-0 pointer-events-none"
                style={{ width: '100%', height: '100%' }}
              />
            </>
          ) : (
            <div className="h-52 flex items-center justify-center text-gray-600 text-sm">
              Citra tidak tersedia untuk peringatan ini.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function AlertHistory() {
  const [cameras, setCameras] = useState([])
  const [alerts, setAlerts] = useState([])
  const [cameraId, setCameraId] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState(wibDateStr())
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [selectedId, setSelectedId] = useState(null)

  useEffect(() => {
    axios
      .get(`${API}/cameras`, { headers: authHeaders() })
      .then(({ data }) => setCameras(data))
      .catch(console.error)
  }, [])

  async function fetchAlerts(nextOffset, append) {
    setLoading(true)
    try {
      const params = { unread_only: false, limit: LIMIT, offset: nextOffset }
      if (cameraId) params.camera_id = cameraId
      if (startDate) params.start_date = startDate
      if (endDate) params.end_date = endDate

      const { data } = await axios.get(`${API}/alerts`, { headers: authHeaders(), params })
      setAlerts((prev) => (append ? [...prev, ...data] : data))
      setHasMore(data.length === LIMIT)
      setOffset(nextOffset)
    } catch (err) {
      console.error('Failed to fetch alert history:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAlerts(0, false)
  }, [cameraId, startDate, endDate]) // eslint-disable-line

  return (
    <div className="min-h-screen bg-gray-950 text-white flex flex-col">
      <header className="bg-gray-900 border-b border-gray-700 px-6 py-3 flex items-center gap-4 flex-shrink-0">
        <Link to="/dashboard" className="text-sm text-gray-400 hover:text-white">
          &larr; Dashboard
        </Link>
        <span className="font-semibold text-white">Riwayat Peringatan Keramaian</span>
      </header>

      <main className="flex-1 overflow-y-auto p-4 max-w-4xl w-full mx-auto">
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <select
            value={cameraId}
            onChange={(e) => setCameraId(e.target.value)}
            className="bg-gray-800 border border-gray-600 text-white text-xs rounded-lg px-2 py-1.5
                       focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="">Semua Kamera</option>
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <input
            type="date"
            value={startDate}
            max={wibDateStr()}
            onChange={(e) => setStartDate(e.target.value)}
            className="bg-gray-800 border border-gray-600 text-white text-xs rounded-lg px-2 py-1.5
                       focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <span className="text-gray-500 text-xs">s/d</span>
          <input
            type="date"
            value={endDate}
            max={wibDateStr()}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-gray-800 border border-gray-600 text-white text-xs rounded-lg px-2 py-1.5
                       focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {alerts.length === 0 && !loading ? (
          <div className="text-center py-16 text-gray-600 text-sm">
            Tidak ada peringatan pada rentang ini.
          </div>
        ) : (
          <div className="space-y-2">
            {alerts.map((alert) => (
              <button
                key={alert.id}
                onClick={() => setSelectedId(alert.id)}
                className="w-full text-left bg-gray-900 hover:bg-gray-800 border border-gray-700
                           rounded-xl p-3 transition-colors flex items-start justify-between gap-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-white truncate">
                    {alert.camera_name || `Kamera ${alert.camera_id}`}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">{formatWib(alert.timestamp)} WIB</p>
                  {alert.description && (
                    <p className="text-xs text-gray-400 mt-1 line-clamp-2">{alert.description}</p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <AlertTypeBadge type={alert.alert_type} />
                  <span className="text-xs text-gray-500">Nilai: {alert.trigger_value}</span>
                  {alert.image_path && <span className="text-xs text-blue-400">Lihat citra</span>}
                </div>
              </button>
            ))}
          </div>
        )}

        {hasMore && (
          <button
            onClick={() => fetchAlerts(offset + LIMIT, true)}
            disabled={loading}
            className="w-full mt-3 text-xs text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700
                       py-2 rounded-lg transition-colors disabled:opacity-50"
          >
            {loading ? 'Memuat...' : 'Muat lebih banyak'}
          </button>
        )}
      </main>

      {selectedId && <AlertDetail alertId={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  )
}
