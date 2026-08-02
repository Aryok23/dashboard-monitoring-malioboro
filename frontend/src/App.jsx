import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AlertHistory from './pages/AlertHistory'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'

function ProtectedRoute({ children }) {
  const token = localStorage.getItem('access_token')
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return children
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/alerts/history"
          element={
            <ProtectedRoute>
              <AlertHistory />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
