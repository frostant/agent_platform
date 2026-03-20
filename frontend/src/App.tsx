import { useState, useEffect, useCallback } from 'react'
import type { Agent } from './types'
import { fetchAgents, login as apiLogin, getMe } from './lib/api'
import Sidebar from './components/Sidebar'
import LoginModal from './components/LoginModal'
import Home from './pages/Home'
import AgentView from './pages/AgentView'
import Admin from './pages/Admin'

type View = 'home' | 'agent' | 'admin'

export default function App() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [role, setRole] = useState('guest')
  const [view, setView] = useState<View>('home')
  const [activeAgentId, setActiveAgentId] = useState<string | null>(null)
  const [showLogin, setShowLogin] = useState(false)
  const [loginError, setLoginError] = useState('')

  const loadAgents = useCallback(async () => {
    try {
      const data = await fetchAgents()
      setAgents(data)
    } catch (e) {
      console.error('加载 Agent 列表失败', e)
    }
  }, [])

  const checkAuth = useCallback(async () => {
    const token = localStorage.getItem('token')
    if (!token) return
    try {
      const data = await getMe()
      setRole(data.role)
    } catch {
      localStorage.removeItem('token')
    }
  }, [])

  useEffect(() => {
    checkAuth()
    loadAgents()
    const timer = setInterval(loadAgents, 10000)
    return () => clearInterval(timer)
  }, [checkAuth, loadAgents])

  // 登录后刷新 Agent 列表（可能有 root_only 的新增显示）
  useEffect(() => {
    loadAgents()
  }, [role, loadAgents])

  const handleLogin = async (password: string) => {
    setLoginError('')
    try {
      const data = await apiLogin(password)
      localStorage.setItem('token', data.token)
      setRole('root')
      setShowLogin(false)
    } catch {
      setLoginError('密码错误')
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    setRole('guest')
    setView('home')
    setActiveAgentId(null)
  }

  const handleSelect = (id: string | null) => {
    if (id === null) {
      setView('home')
      setActiveAgentId(null)
    } else {
      const agent = agents.find(a => a.id === id)
      if (agent && agent.access === 'root_only' && role !== 'root') return
      setView('agent')
      setActiveAgentId(id)
    }
  }

  const activeAgent = agents.find(a => a.id === activeAgentId)

  return (
    <div className="flex min-h-screen bg-gray-100">
      <Sidebar
        agents={agents}
        activeId={activeAgentId}
        role={role}
        onSelect={handleSelect}
        onAdmin={() => { setView('admin'); setActiveAgentId(null) }}
        onLogin={() => setShowLogin(true)}
        onLogout={handleLogout}
      />

      <main className="ml-56 flex-1">
        {view === 'home' && (
          <Home agents={agents} role={role} onSelect={(id) => handleSelect(id)} />
        )}
        {view === 'agent' && activeAgent && (
          <AgentView agent={activeAgent} role={role} onRefresh={loadAgents} />
        )}
        {view === 'admin' && role === 'root' && (
          <Admin agents={agents} onRefresh={loadAgents} />
        )}
      </main>

      {showLogin && (
        <LoginModal
          onLogin={handleLogin}
          onClose={() => { setShowLogin(false); setLoginError('') }}
          error={loginError}
        />
      )}
    </div>
  )
}
