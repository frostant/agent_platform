import type { Agent } from '../types'
import AgentCard from '../components/AgentCard'

interface Props {
  agents: Agent[]
  role: string
  onSelect: (id: string) => void
}

export default function Home({ agents, role, onSelect }: Props) {
  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800">我的 Agent 工作台</h1>
        <p className="text-sm text-gray-500 mt-1">
          共 {agents.length} 个 Agent · {agents.filter(a => a.status === 'running').length} 个运行中
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {agents.map((agent) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            role={role}
            onClick={() => onSelect(agent.id)}
          />
        ))}
      </div>

      {agents.length === 0 && (
        <div className="text-center py-20 text-gray-400">
          <p className="text-lg">暂无 Agent</p>
          <p className="text-sm mt-1">在 agents/ 目录下添加 Agent 并重启网关</p>
        </div>
      )}
    </div>
  )
}
