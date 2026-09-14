import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import ConsolePage from './pages/ConsolePage'
import MetricsPage from './pages/MetricsPage'
import LedgerPage from './pages/LedgerPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/console" replace />} />
        <Route path="/console" element={<ConsolePage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/ledger" element={<LedgerPage />} />
        <Route path="*" element={<Navigate to="/console" replace />} />
      </Route>
    </Routes>
  )
}
