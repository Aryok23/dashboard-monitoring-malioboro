import { useEffect, useRef, useState } from 'react'

const BACKEND = 'http://localhost:8000'

const CLASS_COLORS = {
  people:     '#3b82f6',   // blue
  bicycle:    '#22c55e',   // green
  motorcycle: '#eab308',   // yellow
  car:        '#f97316',   // orange
  bus:        '#ef4444',   // red
  truck:      '#a855f7',   // purple
  bajaj:      '#06b6d4',   // cyan
  becak:      '#06b6d4',
  andong:     '#06b6d4',
}

function drawDetections(canvas, el, detections, frameSize) {
  const ctx = canvas.getContext('2d')

  // Match canvas pixel size to the img element's displayed size
  const elemW = el.clientWidth
  const elemH = el.clientHeight
  canvas.width = elemW
  canvas.height = elemH
  ctx.clearRect(0, 0, elemW, elemH)

  if (!detections || detections.length === 0) return

  // Original frame dimensions used for inference
  const srcW = frameSize?.width  || 1280
  const srcH = frameSize?.height || 720

  // Calculate the rendered video area inside the element (object-fit: contain)
  const videoAspect = srcW / srcH
  const elemAspect  = elemW / elemH

  let drawW, drawH, offsetX, offsetY
  if (videoAspect > elemAspect) {
    // Black bars top & bottom
    drawW   = elemW
    drawH   = elemW / videoAspect
    offsetX = 0
    offsetY = (elemH - drawH) / 2
  } else {
    // Black bars left & right
    drawW   = elemH * videoAspect
    drawH   = elemH
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

    // Bounding box
    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.strokeRect(rx, ry, rw, rh)

    // Label
    const label = `${det.class_name} ${Math.round(det.confidence * 100)}%`
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

export default function LiveFeed({ camera, connected, detections = [], frameSize }) {
  const imgRef    = useRef(null)
  const canvasRef = useRef(null)
  const [videoReady, setVideoReady] = useState(false)
  const [videoError, setVideoError] = useState(false)

  // ── Reset ready state on camera switch ──────────────────────────────────
  useEffect(() => {
    setVideoReady(false)
    setVideoError(false)
  }, [camera?.id])

  // ── Bounding box canvas ──────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    const img    = imgRef.current
    if (!canvas || !img || !videoReady) return
    drawDetections(canvas, img, detections, frameSize)
  }, [detections, frameSize, videoReady])

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="relative bg-gray-900 rounded-xl overflow-hidden border border-gray-700 flex-shrink-0">

      {/* MJPEG feed */}
      <img
        ref={imgRef}
        src={camera ? `${BACKEND}/stream/mjpeg/${camera.id}` : undefined}
        alt=""
        className={`w-full object-contain max-h-[460px] ${videoReady ? 'block' : 'hidden'}`}
        onLoad={() => { setVideoReady(true); setVideoError(false) }}
        onError={() => setVideoError(true)}
      />

      {/* Bbox canvas — sits exactly over the video */}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 pointer-events-none"
        style={{ width: '100%', height: '100%' }}
      />

      {/* Loading / error state */}
      {!videoReady && (
        <div className="w-full h-72 flex items-center justify-center bg-gray-950">
          <div className="text-center">
            {videoError ? (
              <>
                <svg className="w-12 h-12 text-red-700 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-red-500 text-sm">Stream tidak tersedia</p>
              </>
            ) : (
              <>
                <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                <p className="text-gray-500 text-sm">
                  {camera ? `Memuat ${camera.name}...` : 'Pilih kamera'}
                </p>
              </>
            )}
          </div>
        </div>
      )}

      {/* Top-left: camera name + zone */}
      {camera && (
        <div className="absolute top-3 left-3 flex items-center gap-2">
          <span className="bg-black/70 text-white text-xs font-semibold px-2.5 py-1 rounded-full backdrop-blur-sm">
            {camera.name}
          </span>
          <span className="bg-blue-600/80 text-white text-xs px-2 py-1 rounded-full backdrop-blur-sm">
            {camera.zone}
          </span>
          {camera.is_ptz && (
            <span className="bg-purple-600/80 text-white text-xs px-2 py-1 rounded-full backdrop-blur-sm">
              PTZ
            </span>
          )}
        </div>
      )}

      {/* Top-right: LIVE indicator */}
      <div className="absolute top-3 right-3">
        {connected ? (
          <div className="flex items-center gap-1.5 bg-black/70 px-2.5 py-1 rounded-full backdrop-blur-sm">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
            </span>
            <span className="text-green-400 text-xs font-bold tracking-wider">LIVE</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 bg-black/70 px-2.5 py-1 rounded-full backdrop-blur-sm">
            <span className="h-2 w-2 rounded-full bg-red-500" />
            <span className="text-red-400 text-xs font-medium">Reconnecting...</span>
          </div>
        )}
      </div>

      {/* Bottom-right: bbox color legend */}
      {videoReady && detections.length > 0 && (
        <div className="absolute bottom-3 right-3 bg-black/70 backdrop-blur-sm rounded-lg px-3 py-2 flex flex-col gap-1">
          {[...new Set(detections.map(d => d.class_name))].map(cls => (
            <div key={cls} className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: CLASS_COLORS[cls] || '#fff' }} />
              <span className="text-white text-xs">{cls}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
