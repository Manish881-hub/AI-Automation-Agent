import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'
import { apiUrl } from './api'

type Run = {
  run_id: string
  status: string
  objective: string
  url: string
  plan?: { steps: any[] }
  results: any[]
  analysis?: any
  report?: string
}

function App() {
  const [url, setUrl] = useState('https://example.com')
  const [objective, setObjective] = useState('verify that the homepage loads and contains the expected Example Domain text')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  async function startRun(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setRun(null)
    setEvents([])
    const res = await fetch(apiUrl('/api/runs'), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, objective})
    })
    const data = await res.json()
    setRun(data)
    setLoading(false)
  }

  useEffect(() => {
    if (!run?.run_id) return
    const timer = setInterval(async () => {
      const [r, e] = await Promise.all([
        fetch(apiUrl(`/api/runs/${run.run_id}`)),
        fetch(apiUrl(`/api/runs/${run.run_id}/events`))
      ])
      setRun(await r.json())
      setEvents((await e.json()).events)
    }, 1200)
    return () => clearInterval(timer)
  }, [run?.run_id])

  return <main>
    <header>
      <div><span className="eyebrow">autonomous qa</span><h1>ai test automation agent</h1><p>plan → execute → validate → debug → report</p></div>
      <div className="status">{run?.status || 'idle'}</div>
    </header>

    <section className="card">
      <h2>new test run</h2>
      <form onSubmit={startRun}>
        <label>client website<input value={url} onChange={e => setUrl(e.target.value)} /></label>
        <label>test objective<textarea value={objective} onChange={e => setObjective(e.target.value)} /></label>
        <button disabled={loading}>{loading ? 'starting…' : 'run autonomous test'}</button>
      </form>
    </section>

    {run && <>
      <section className="grid">
        <div className="card"><h2>agent timeline</h2><div className="timeline">{events.map((x,i)=><div key={i}>{x}</div>)}</div></div>
        <div className="card"><h2>test results</h2>{run.results?.length ? run.results.map(r=><div className="result" key={r.step_id}><span className={r.status}>{r.status}</span><b>step {r.step_id}</b><span>{r.message}</span></div>) : <p>waiting for execution…</p>}</div>
      </section>
      {run.analysis && <section className="card"><h2>debugger analysis</h2><p>{run.analysis.summary}</p><p><b>probable root cause:</b> {run.analysis.probable_root_cause}</p><h3>evidence</h3><ul>{run.analysis.evidence?.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul><h3>recommended actions</h3><ul>{run.analysis.recommended_actions?.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul></section>}
      {run.report && <section className="card"><h2>report</h2><pre>{run.report}</pre></section>}
    </>}
  </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
