import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import ConsolePage from './pages/ConsolePage'
import LearningHomePage from './pages/LearningHomePage'
import LedgerPage from './pages/LedgerPage'
import MetricsPage from './pages/MetricsPage'
import StudioPage from './pages/StudioPage'
import ToolPlaceholderPage from './pages/ToolPlaceholderPage'

/**
 * 路由：教育门面为主入口，实时链路工作台作为「数字人对话」二级视图保留
 * （工程能力证明仍在：会话工作台 / 指标看板 / 评估台账）。
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<LearningHomePage />} />
        <Route path="/tools/:key" element={<ToolPlaceholderPage />} />
        <Route path="/console" element={<ConsolePage />} />
        {/* 技术详情（状态机/事件流/延迟瀑布/口型统计）：与学习者界面分开，侧栏「工程视图」入口 */}
        <Route path="/studio" element={<StudioPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/ledger" element={<LedgerPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
