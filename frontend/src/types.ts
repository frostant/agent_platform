export interface Agent {
  id: string
  name: string
  description: string
  icon: string
  type: 'fastapi' | 'streamlit' | 'static'
  port: number | null
  access: 'public' | 'root_only'
  status: 'running' | 'stopped' | 'error'
  tags: string[]
}
