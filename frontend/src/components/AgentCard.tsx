import type { Agent } from '../types'

const ICON_MAP: Record<string, string> = {
  send: '📨', box: '📦', utensils: '🍽', flask: '🧪',
  'bar-chart': '📊', 'trending-up': '📈', edit: '✍️',
  workflow: '⚙️',
}

const STATUS_MAP = {
  running: { label: '运行中', color: 'bg-green-500', textColor: 'text-green-700', bgColor: 'bg-green-50' },
  stopped: { label: '已停止', color: 'bg-gray-300', textColor: 'text-gray-500', bgColor: 'bg-gray-50' },
  error: { label: '异常', color: 'bg-red-500', textColor: 'text-red-700', bgColor: 'bg-red-50' },
}

interface Props {
  agent: Agent
  role: string
  onClick: () => void
}

export default function AgentCard({ agent, role, onClick }: Props) {
  const s = STATUS_MAP[agent.status]
  const locked = agent.access === 'root_only' && role !== 'root'

  return (
    <div
      onClick={locked ? undefined : onClick}
      className={`bg-white rounded-xl border border-gray-200 p-5 transition-all ${
        locked
          ? 'opacity-60 cursor-not-allowed'
          : 'hover:shadow-md hover:border-blue-200 cursor-pointer'
      }`}
    >
      <div className="flex items-start justify-between mb-3">
        <span className="text-3xl">{ICON_MAP[agent.icon] || '📦'}</span>
        <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs ${s.bgColor} ${s.textColor}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${s.color}`} />
          {s.label}
        </div>
      </div>

      <h3 className="font-semibold text-gray-800 mb-1">
        {agent.name}
        {locked && <span className="ml-1.5 text-sm">🔒</span>}
      </h3>
      <p className="text-sm text-gray-500 mb-3 line-clamp-2">{agent.description}</p>

      <div className="flex flex-wrap gap-1.5">
        {agent.tags.map((tag) => (
          <span key={tag} className="px-2 py-0.5 bg-gray-100 text-gray-500 rounded text-xs">
            {tag}
          </span>
        ))}
      </div>
    </div>
  )
}
