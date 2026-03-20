import { Bot, Settings, LogIn, LogOut } from 'lucide-react'
import type { Agent } from '../types'

interface Props {
  agents: Agent[]
  activeId: string | null
  role: string
  onSelect: (id: string | null) => void
  onAdmin: () => void
  onLogin: () => void
  onLogout: () => void
}

const ICON_MAP: Record<string, string> = {
  send: '📨', box: '📦', utensils: '🍽', flask: '🧪',
  'bar-chart': '📊', 'trending-up': '📈', edit: '✍️',
  workflow: '⚙️',
}

export default function Sidebar({ agents, activeId, role, onSelect, onAdmin, onLogin, onLogout }: Props) {
  return (
    <aside className="w-56 bg-gray-50 border-r border-gray-200 flex flex-col h-screen fixed left-0 top-0">
      {/* Logo */}
      <div
        className="px-4 py-4 border-b border-gray-200 cursor-pointer hover:bg-gray-100"
        onClick={() => onSelect(null)}
      >
        <div className="flex items-center gap-2">
          <Bot className="w-6 h-6 text-blue-600" />
          <span className="font-semibold text-gray-800 text-sm">Agent Platform</span>
        </div>
      </div>

      {/* Agent 列表 */}
      <div className="flex-1 overflow-y-auto py-2">
        <div className="px-3 py-1 text-xs font-medium text-gray-400 uppercase tracking-wider">
          Agent
        </div>
        {agents.map((agent) => (
          <div
            key={agent.id}
            onClick={() => onSelect(agent.id)}
            className={`mx-2 px-3 py-2 rounded-lg cursor-pointer flex items-center gap-2 text-sm transition-colors ${
              activeId === agent.id
                ? 'bg-blue-50 text-blue-700 border border-blue-200'
                : 'hover:bg-gray-100 text-gray-700'
            }`}
          >
            <span className="text-base">{ICON_MAP[agent.icon] || '📦'}</span>
            <span className="flex-1 truncate">{agent.name}</span>
            {agent.status === 'running' && (
              <span className="w-2 h-2 rounded-full bg-green-500 flex-shrink-0" />
            )}
            {agent.status === 'stopped' && (
              <span className="w-2 h-2 rounded-full bg-gray-300 flex-shrink-0" />
            )}
            {agent.status === 'error' && (
              <span className="w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
            )}
            {agent.access === 'root_only' && role !== 'root' && (
              <span className="text-xs text-gray-400">🔒</span>
            )}
          </div>
        ))}
      </div>

      {/* 底部：管理 + 登录 */}
      <div className="border-t border-gray-200 p-2">
        {role === 'root' && (
          <button
            onClick={onAdmin}
            className="w-full px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 flex items-center gap-2"
          >
            <Settings className="w-4 h-4" />
            管理面板
          </button>
        )}
        {role === 'root' ? (
          <button
            onClick={onLogout}
            className="w-full px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 flex items-center gap-2"
          >
            <LogOut className="w-4 h-4" />
            <span className="flex-1 text-left">退出</span>
            <span className="text-xs bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded">root</span>
          </button>
        ) : (
          <button
            onClick={onLogin}
            className="w-full px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 flex items-center gap-2"
          >
            <LogIn className="w-4 h-4" />
            <span>管理员登录</span>
          </button>
        )}
      </div>
    </aside>
  )
}
