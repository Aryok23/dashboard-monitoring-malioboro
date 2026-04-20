import Hls from 'hls.js'
import { useEffect, useRef, useState } from 'react'

const BACKEND = 'http://localhost:8000'

export default function LiveFeed({ camera, connected }) {
  const videoRef = useRef(null)
  const hlsRef = useRef(null)
  const [videoReady, setVideoReady] = useState(false)
  const [videoError, setVideoError] = useState(false)

  useEffect(() => {
    if (!camera) return

    const video = videoRef.current
    if (!video) return

    setVideoReady(false)
    setVideoError(false)

    // Destroy any previous hls instance
    if (hlsRef.current) {
      hlsRef.current.destroy()
      hlsRef.current = null
    }

    const streamUrl = `${BACKEND}/proxy/hls/${camera.id}/master.m3u8`

    if (Hls.isSupported()) {
      const hls = new Hls({
        lowLatencyMode: true,
        backBufferLength: 0,       // don't keep old segments in memory
        maxBufferLength: 8,        // keep at most 8s buffered ahead
        maxMaxBufferLength: 15,
        liveSyncDurationCount: 2,  // try to stay close to live edge
        liveMaxLatencyDurationCount: 5,
      })

      hls.loadSource(streamUrl)
      hls.attachMedia(video)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {})
        setVideoReady(true)
        setVideoError(false)
      })

      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          setVideoError(true)
          // Fatal errors: try to recover once, then reload source
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            hls.startLoad()
          } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            hls.recoverMediaError()
          } else {
            hls.loadSource(streamUrl)
          }
        }
      })

      hlsRef.current = hls
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari native HLS
      video.src = streamUrl
      video.addEventListener('loadeddata', () => setVideoReady(true), { once: true })
      video.play().catch(() => {})
    } else {
      setVideoError(true)
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
    }
  }, [camera?.id]) // re-init only when the camera changes

  return (
    <div className="relative bg-gray-900 rounded-xl overflow-hidden border border-gray-700 flex-shrink-0">
      {/* Video element — always mounted so hls.js can attach */}
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className={`w-full object-contain max-h-[460px] ${videoReady ? 'block' : 'hidden'}`}
      />

      {/* Loading / error placeholder */}
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

      {/* Top-right: detection WS indicator */}
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
    </div>
  )
}
