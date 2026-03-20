import { useState } from 'react'
import { RotateCw, Square, Play, RefreshCw } from 'lucide-react'
import type { Agent } from '../types'
import { agentAction, reloadAgents } from '../lib/api'

interface Props {
  agents: Agent[]
  onRefresh: () => void
}

const STATUS = {
  running: { label: '运行中', dot: 'bg-green-500', text: 'text-green-700' },
  stopped: { label: '已停止', dot: 'bg-gray-300', text: 'text-gray-500' },
  error: { label: '异常', dot: 'bg-red-500', text: 'text-red-700' },
}

export default function Admin({ agents, onRefresh }: Props) {
  const [loading, setLoading] = useState<Record<string, string>>({})

  const handleAction = async (id: string, action: 'start' | 'stop' | 'restart') => {
    setLoading((l) => ({ ...l, [id]: action }))
    try {
      await agentAction(id, action)
      setTimeout(onRefresh, 1000)
    } finally {
      setLoading((l) => ({ ...l, [id]: '' }))
    }
  }

  const handleReload = async () => {
    await reloadAgents()
    onRefresh()
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Agent 管理面板</h1>
          <p className="text-sm text-gray-500 mt-1">管理所有 Agent 的运行状态</p>
        </div>
        <button
          onClick={handleReload}
          className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200 flex items-center gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          重新扫描
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase">Agent</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase">类型</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase">端口</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase">权限</th>
              <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase">状态</th>
              <th className="text-right px-5 py-3 text-xs font-medium text-gray-500 uppercase">操作</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => {
              const s = STATUS[agent.status]
              const l = loading[agent.id]
              return (
                <tr key={agent.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <div className="font-medium text-sm text-gray-800">{agent.name}</div>
                    <div className="text-xs text-gray-400">{agent.id}</div>
                  </td>
                  <td className="px-5 py-3 text-sm text-gray-500">{agent.type}</td>
                  <td className="px-5 py-3 text-sm text-gray-500 font-mono">{agent.port || '-'}</td>
                  <td className="px-5 py-3 text-sm text-gray-500">
                    {agent.access === 'root_only' ? '🔒 仅管理员' : '公开'}
                  </td>
                  <td className="px-5 py-3">
                    <span className={`inline-flex items-center gap-1.5 text-xs ${s.text}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                      {s.label}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {agent.status !== 'running' && (
                        <button
                          onClick={() => handleAction(agent.id, 'start')}
                          disabled={!!l}
                          className="p-1.5 rounded hover:bg-green-50 text-green-600 disabled:opacity-50"
                          title="启动"
                        >
                          <Play className="w-4 h-4" />
                        </button>
                      )}
                      {agent.status === 'running' && (
                        <button
                          onClick={() => handleAction(agent.id, 'stop')}
                          disabled={!!l}
                          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 disabled:opacity-50"
                          title="停止"
                        >
                          <Square className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => handleAction(agent.id, 'restart')}
                        disabled={!!l}
                        className="p-1.5 rounded hover:bg-blue-50 text-blue-600 disabled:opacity-50"
                        title="重启"
                      >
                        <RotateCw className={`w-4 h-4 ${l === 'restart' ? 'animate-spin' : ''}`} />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
