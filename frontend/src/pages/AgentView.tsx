import { useState } from 'react'
import { RotateCw, Square, Play, ExternalLink } from 'lucide-react'
import type { Agent } from '../types'
import { agentAction } from '../lib/api'

interface Props {
  agent: Agent
  role: string
  onRefresh: () => void
}

const STATUS_MAP = {
  running: { label: '运行中', dotColor: 'bg-green-500', textColor: 'text-green-700' },
  stopped: { label: '已停止', dotColor: 'bg-gray-300', textColor: 'text-gray-500' },
  error: { label: '异常', dotColor: 'bg-red-500', textColor: 'text-red-700' },
}

export default function AgentView({ agent, role, onRefresh }: Props) {
  const [loading, setLoading] = useState('')
  const s = STATUS_MAP[agent.status]

  const handleAction = async (action: 'start' | 'stop' | 'restart') => {
    setLoading(action)
    try {
      await agentAction(agent.id, action)
      setTimeout(onRefresh, 1000)
    } finally {
      setLoading('')
    }
  }

  const agentUrl = agent.port ? `http://localhost:${agent.port}` : null

  return (
    <div className="flex flex-col h-screen">
      {/* 顶部工具栏 */}
      <div className="px-6 py-3 border-b border-gray-200 bg-white flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-gray-800">{agent.name}</h2>
          <div className={`flex items-center gap-1.5 text-xs ${s.textColor}`}>
            <span className={`w-2 h-2 rounded-full ${s.dotColor}`} />
            {s.label}
          </div>
          {agent.port && (
            <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">
              :{agent.port}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {role === 'root' && (
            <>
              {agent.status !== 'running' && (
                <button
                  onClick={() => handleAction('start')}
                  disabled={!!loading}
                  className="px-3 py-1.5 text-xs bg-green-50 text-green-700 rounded-lg hover:bg-green-100 flex items-center gap-1 disabled:opacity-50"
                >
                  <Play className="w-3 h-3" />
                  {loading === 'start' ? '启动中...' : '启动'}
                </button>
              )}
              {agent.status === 'running' && (
                <button
                  onClick={() => handleAction('stop')}
                  disabled={!!loading}
                  className="px-3 py-1.5 text-xs bg-gray-50 text-gray-600 rounded-lg hover:bg-gray-100 flex items-center gap-1 disabled:opacity-50"
                >
                  <Square className="w-3 h-3" />
                  {loading === 'stop' ? '停止中...' : '停止'}
                </button>
              )}
              <button
                onClick={() => handleAction('restart')}
                disabled={!!loading}
                className="px-3 py-1.5 text-xs bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 flex items-center gap-1 disabled:opacity-50"
              >
                <RotateCw className="w-3 h-3" />
                {loading === 'restart' ? '重启中...' : '重启'}
              </button>
            </>
          )}
          {agentUrl && (
            <a
              href={agentUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="px-3 py-1.5 text-xs text-gray-500 rounded-lg hover:bg-gray-50 flex items-center gap-1"
            >
              <ExternalLink className="w-3 h-3" />
              新窗口打开
            </a>
          )}
        </div>
      </div>

      {/* Agent iframe */}
      {agent.status === 'running' && agentUrl ? (
        <iframe
          src={agentUrl}
          className="flex-1 w-full border-0"
          title={agent.name}
        />
      ) : (
        <div className="flex-1 flex items-center justify-center bg-gray-50">
          <div className="text-center text-gray-400">
            <p className="text-lg mb-2">Agent 未运行</p>
            {role === 'root' ? (
              <button
                onClick={() => handleAction('start')}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
              >
                启动 Agent
              </button>
            ) : (
              <p className="text-sm">请联系管理员启动</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
