import { lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import LearningHomePage from './pages/LearningHomePage'

/**
 * 路由：产品视角为主入口，实时链路工作台作为「工程视图」收在侧栏二级分组。
 *
 * 信息架构（2026-09-23 重构）：
 *   产品视角  /           学习首页（真实运行数据总览，ADR-009）
 *            /console     对话辅导（数字人舞台 + 对话流）
 *            /insights    学习洞察（真实本地对话记录统计）
 *            /library     数字人资产台（形象 / 音色 / 讲解产物 / 服务状态）
 *   工程视角  /studio /metrics /ledger（验收证据链，默认不外露）
 *
 * 已下线：`/tools/:key`（错题集合 / 视频讲解 / 知识库 / 阶段目标 4 个占位页）——
 * 全部没有后端数据源，点进去只有「规划要点」文字，是用户反馈「多数内容是占位卡片」
 * 的直接来源；其能力已由 `/insights`（真数据）+ 页尾「待接入能力」承接。
 *
 * 拆包口径：首屏只静态引入学习首页，其余页面 **按需加载**。
 * 加载态由 AppShell 内的 Suspense 兜住（侧栏不闪，只有内容区显示占位）。
 */
const ConsolePage = lazy(() => import('./pages/ConsolePage'))
const InsightsPage = lazy(() => import('./pages/InsightsPage'))
const LibraryPage = lazy(() => import('./pages/LibraryPage'))
const StudioPage = lazy(() => import('./pages/StudioPage'))
const MetricsPage = lazy(() => import('./pages/MetricsPage'))
const LedgerPage = lazy(() => import('./pages/LedgerPage'))

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<LearningHomePage />} />
        <Route path="/console" element={<ConsolePage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/library" element={<LibraryPage />} />
        {/* 工程视图（状态机/事件流/延迟瀑布/口型统计）：与学习者界面分开 */}
        <Route path="/studio" element={<StudioPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
        <Route path="/ledger" element={<LedgerPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
