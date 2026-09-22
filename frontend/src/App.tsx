import { lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import LearningHomePage from './pages/LearningHomePage'

/**
 * 路由：教育门面为主入口，实时链路工作台作为「数字人对话」二级视图保留
 * （工程能力证明仍在：会话工作台 / 指标看板 / 评估台账）。
 *
 * 拆包口径：首屏只静态引入学习首页，其余页面 **按需加载**。此前全部静态 import，
 * 构建产物是单个 591 KB 的 JS —— 技术页的表格/链路代码和首页图表全挤在首包里。
 * now：每个页面各自成块，进哪个页面才付哪个页面的钱。
 * 加载态由 AppShell 内的 Suspense 兜住（侧栏不闪，只有内容区显示占位）。
 */
const ConsolePage = lazy(() => import('./pages/ConsolePage'))
const StudioPage = lazy(() => import('./pages/StudioPage'))
const MetricsPage = lazy(() => import('./pages/MetricsPage'))
const LedgerPage = lazy(() => import('./pages/LedgerPage'))
const ToolPlaceholderPage = lazy(() => import('./pages/ToolPlaceholderPage'))

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
