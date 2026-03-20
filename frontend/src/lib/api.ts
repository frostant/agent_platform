const BASE = '/api'

function getHeaders(): Record<string, string> {
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

export async function fetchAgents() {
  const res = await fetch(`${BASE}/agents`, { headers: getHeaders() })
  return res.json()
}

export async function fetchAgent(id: string) {
  const res = await fetch(`${BASE}/agents/${id}`, { headers: getHeaders() })
  return res.json()
}

export async function login(password: string) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (!res.ok) throw new Error('密码错误')
  return res.json()
}

export async function getMe() {
  const res = await fetch(`${BASE}/auth/me`, { headers: getHeaders() })
  return res.json()
}

export async function agentAction(id: string, action: 'start' | 'stop' | 'restart') {
  const res = await fetch(`${BASE}/agents/${id}/${action}`, {
    method: 'POST',
    headers: getHeaders(),
  })
  return res.json()
}

export async function reloadAgents() {
  const res = await fetch(`${BASE}/agents/reload`, {
    method: 'POST',
    headers: getHeaders(),
  })
  return res.json()
}
