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

const API = 'http://localhost:8000'
const TABS = [
  { key: 'today', label: 'Hari Ini' },
  { key: '7days', label: '7 Hari' },
  { key: 'heatmap', label: 'Per Jam' },
]

function authHeaders() {
  return { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
}

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

export default function CrowdChart({ activeCamera, cameras = [] }) {
  const [tab, setTab] = useState('today')
  const [data, setData] = useState([])
  const [chartCameraId, setChartCameraId] = useState(1)
  const [loading, setLoading] = useState(false)

  // Keep chart camera in sync with active camera when it changes
  useEffect(() => {
    if (activeCamera) setChartCameraId(activeCamera.id)
  }, [activeCamera])

  useEffect(() => {
    fetchData()
  }, [tab, chartCameraId]) // eslint-disable-line

  async function fetchData() {
    setLoading(true)
    setData([])
    try {
      let url = ''
      if (tab === 'today') {
        url = `${API}/detections/history?camera_id=${chartCameraId}&date=${todayStr()}`
        const { data: rows } = await axios.get(url, { headers: authHeaders() })
        setData(
          rows.map((r) => ({
            time: r.timestamp.slice(11, 16),
            total: r.total_count,
          }))
        )
      } else if (tab === '7days') {
        url = `${API}/detections/summary?camera_id=${chartCameraId}&range=7`
        const { data: rows } = await axios.get(url, { headers: authHeaders() })
        setData(rows.map((r) => ({ time: r.date, total: r.total })))
      } else {
        url = `${API}/detections/heatmap?date=${todayStr()}`
        const { data: rows } = await axios.get(url, { headers: authHeaders() })
        const filtered = rows.filter((r) => r.camera_id === chartCameraId)
        setData(
          filtered.map((r) => ({
            time: `${String(r.hour).padStart(2, '0')}:00`,
            total: r.avg_count,
          }))
        )
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
        <h3 className="text-sm font-semibold text-white">Grafik Kepadatan</h3>
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
      ) : data.length === 0 ? (
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
              dataKey="total"
              name="Total Objek"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: '#3b82f6' }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
