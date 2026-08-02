import axios from 'axios'
import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { wibDateStr } from '../utils/wib'

const API = import.meta.env.VITE_API_URL
const TABS = [
  { key: 'daily', label: 'Harian (Per Jam)' },
  { key: 'weekly', label: 'Mingguan (Per Hari)' },
]

function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
}

export default function CrowdChart({ activeCamera, cameras = [] }) {
  const [tab, setTab] = useState('daily')
  const [data, setData] = useState([])
  const [chartCameraId, setChartCameraId] = useState(1)
  const [selectedDate, setSelectedDate] = useState(wibDateStr())
  const [loading, setLoading] = useState(false)

  // Keep chart camera in sync with active camera when it changes
  useEffect(() => {
    if (activeCamera) setChartCameraId(activeCamera.id)
  }, [activeCamera])

  useEffect(() => {
    fetchData()
    // New rows only land every 300s (see backend _periodic_log_writer), so
    // polling faster than that wouldn't surface new data any sooner. Silent
    // (no loading spinner / no clearing) so the chart doesn't flash on poll.
    const interval = setInterval(() => fetchData({ silent: true }), 60_000)
    return () => clearInterval(interval)
  }, [tab, chartCameraId, selectedDate]) // eslint-disable-line

  async function fetchData({ silent = false } = {}) {
    if (!silent) {
      setLoading(true)
      setData([])
    }
    try {
      if (tab === 'daily') {
        const url = `${API}/detections/hourly?camera_id=${chartCameraId}&date=${selectedDate}`
        const { data: rows } = await axios.get(url, { headers: authHeaders() })
        setData(
          rows.map((r) => ({
            time: `${String(r.hour).padStart(2, '0')}:00`,
            avg: r.avg,
            peak: r.peak,
          }))
        )
      } else {
        const url = `${API}/detections/summary?camera_id=${chartCameraId}&days=7`
        const { data: rows } = await axios.get(url, { headers: authHeaders() })
        setData(rows.map((r) => ({ time: r.date, avg: r.avg, peak: r.peak })))
      }
    } catch (err) {
      console.error('Chart fetch error:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-white">Grafik Kepadatan Pejalan Kaki</h3>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Camera selector */}
          <select
            value={chartCameraId}
            onChange={(e) => setChartCameraId(Number(e.target.value))}
            className="bg-gray-800 border border-gray-600 text-white text-xs rounded-lg px-2 py-1
                       focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          {/* Date picker (daily tab only, WIB calendar date) */}
          {tab === 'daily' && (
            <input
              type="date"
              value={selectedDate}
              max={wibDateStr()}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="bg-gray-800 border border-gray-600 text-white text-xs rounded-lg px-2 py-1
                         focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          )}
          {/* Tab buttons */}
          <div className="flex bg-gray-800 rounded-lg p-0.5 border border-gray-700">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  tab === t.key
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart */}
      {loading ? (
        <div className="h-52 flex items-center justify-center text-gray-500 text-sm">
          Memuat data...
        </div>
      ) : !data.some((d) => d.avg !== null || d.peak !== null) ? (
        <div className="h-52 flex items-center justify-center text-gray-600 text-sm">
          Belum ada data untuk ditampilkan.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey="time"
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              axisLine={{ stroke: '#4b5563' }}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#1f2937',
                border: '1px solid #374151',
                borderRadius: 8,
                color: '#f9fafb',
                fontSize: 12,
              }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: '#9ca3af' }} />
            <Line
              type="monotone"
              dataKey="avg"
              name="Rata-rata Orang"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              activeDot={{ r: 4, fill: '#3b82f6' }}
            />
            <Line
              type="monotone"
              dataKey="peak"
              name="Puncak Orang"
              stroke="#f97316"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              activeDot={{ r: 4, fill: '#f97316' }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
