import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'dh-theme'

/**
 * 主题切换：默认浅色；选择写入 localStorage。
 * data-theme 挂在 <html> 上，样式全部走 tokens.css 变量，组件无需感知主题。
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    return saved === 'dark' ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  return [theme, setTheme] as const
}
