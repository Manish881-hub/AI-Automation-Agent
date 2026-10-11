import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'
import { apiUrl } from './api'
import { getChecklist, getResultsPanel, inputLabel, linkText } from './runSummary'

type Website = {
  url: string
  title: string
  headings: string[]
  links: Array<string | { text?: string; href?: string }>
  buttons: string[]
  inputs: Array<{ label?: string; name?: string; type?: string; placeholder?: string }>
}

type Run = {
  run_id: string
  status: string
  objective: string
  url: string
  plan?: { steps: any[] }
  results: any[]
  analysis?: any
  report?: string
  website?: Website | null
  objective_status?: string
  objective_reason?: string
  error_stage?: string
  error_kind?: string
  error_message?: string
  fix_status?: string
  fix_explanation?: string
  fix_branch?: string
  fix_diff?: string
  fix_verify_summary?: string
}

function App() {
  const [url, setUrl] = useState('https://example.com')
  const [objective, setObjective] = useState('verify that the homepage loads and contains the expected Example Domain text')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [fixBusy, setFixBusy] = useState(false)
  const [fixError, setFixError] = useState('')
  const [cancelBusy, setCancelBusy] = useState(false)
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [tokenRequired, setTokenRequired] = useState(false)
  const [opPassword, setOpPassword] = useState('')
  const [opBusy, setOpBusy] = useState(false)

  async function refreshAuth() {
    try {
      const r = await fetch(apiUrl('/api/auth/status'))
      const d = await r.json()
      setAuthed(!!d.authenticated)
      setTokenRequired(!!d.token_required)
    } catch {
      setAuthed(null)
    }
  }

  async function operatorLogin(e: React.FormEvent) {
    e.preventDefault()
    setOpBusy(true)
    setFixError('')
    try {
      const res = await fetch(apiUrl('/api/auth/login'), {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ token: opPassword })
      })
      if (res.ok) {
        setOpPassword('')
        await refreshAuth()
      } else {
        setFixError('operator sign-in failed')
      }
    } finally {
      setOpBusy(false)
    }
  }

  async function operatorLogout() {
    await fetch(apiUrl('/api/auth/logout'), { method: 'POST' })
    await refreshAuth()
  }

  async function cancelRun() {
    if (!run) return
    setCancelBusy(true)
    setFixError('')
    try {
      const res = await fetch(apiUrl(`/api/runs/${run.run_id}/cancel`), { method: 'POST' })
      if (res.status === 401) {
        setAuthed(false)
        setFixError('cancelling needs operator sign-in')
        return
      }
      const data = await res.json()
      if (!res.ok) {
        setFixError(data.detail || 'cancel request failed')
        return
      }
      setRun(prev => prev ? { ...prev, status: data.status, fix_status: data.fix_status ?? prev.fix_status } : prev)
    } finally {
      setCancelBusy(false)
    }
  }

  async function decideFix(decision: 'approve' | 'reject') {
    if (!run) return
    setFixBusy(true)
    setFixError('')
    try {
      const res = await fetch(apiUrl(`/api/runs/${run.run_id}/fix/${decision}`), { method: 'POST' })
      if (res.status === 401) {
        setAuthed(false)
        setFixError('approval needs operator sign-in')
        return
      }
      const data = await res.json()
      setRun(prev => prev ? { ...prev, fix_status: data.fix_status, fix_branch: data.fix_branch ?? prev.fix_branch } : prev)
    } finally {
      setFixBusy(false)
    }
  }

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
    refreshAuth()
  }, [])

  useEffect(() => {
    if (!run?.run_id) return
    refreshAuth()
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
      <div className="status">{run?.status || 'idle'}{run && (run.status === 'running' || run.status === 'queued') && (
        <button onClick={cancelRun} disabled={cancelBusy}>{cancelBusy ? 'cancelling…' : 'cancel run'}</button>
      )}</div>
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
      {run.objective_status && run.objective_status !== 'unknown' && (
        <section className={`card objective ${run.objective_status}`} role="status">
          <h2>business objective: {run.objective_status}</h2>
          {run.objective_reason && <p>{run.objective_reason}</p>}
        </section>
      )}
      <section className="grid">
        <div className="card">
          <h2>website understanding</h2>
          <p className="muted">observed by reconnaissance before planning</p>
          {run.website ? <>
            <p><b>{run.website.title}</b></p>
            {!!run.website.buttons?.length && <><h3>buttons</h3><ul className="snap-list">{run.website.buttons.map((b,i)=><li key={i}>{b}</li>)}</ul></>}
            {!!run.website.inputs?.length && <><h3>inputs</h3><ul className="snap-list">{run.website.inputs.map((x,i)=><li key={i}>{inputLabel(x)}</li>)}</ul></>}
            {!!run.website.links?.length && <><h3>links</h3><ul className="snap-list">{run.website.links.map((x,i)=><li key={i}>{linkText(x)}</li>)}</ul></>}
          </> : <p>reconnaissance pending…</p>}
        </div>
        <div className="card">
          <h2>agent execution</h2>
          <ul className="checklist">
            {getChecklist(run, events).map(item => <li key={item.id} className={item.state}>
              <span aria-hidden="true">{item.state === 'done' ? '✓' : item.state === 'fail' ? '✕' : item.state === 'skipped' ? '–' : '…'}</span>
              <span>{item.label}</span>
              {item.detail && <span className="muted">{item.detail}</span>}
            </li>)}
          </ul>
        </div>
      </section>
      <section className="grid">
        <div className="card"><h2>agent timeline</h2><div className="timeline">{events.map((x,i)=><div key={i}>{x}</div>)}</div></div>
        <div className="card"><h2>test results</h2>{(() => {
          const panel = getResultsPanel(run)
          if (panel.kind === 'results') return run.results!.map(r=><div className="result" key={r.step_id}><span className={r.status}>{r.status}</span><b>step {r.step_id}</b><span>{r.message}{r.retried ? ' (retried)' : ''}</span></div>)
          if (panel.kind === 'empty') return <><p><b>{panel.headline}</b></p><p className="muted">{panel.detail}</p></>
          return <p>waiting for execution…</p>
        })()}</div>
      </section>
      {run.analysis && <section className="card"><h2>debugger analysis</h2><p>{run.analysis.summary}</p><p><b>probable root cause:</b> {run.analysis.probable_root_cause}</p><h3>evidence</h3><ul>{run.analysis.evidence?.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul><h3>recommended actions</h3><ul>{run.analysis.recommended_actions?.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul></section>}
      {run.fix_status && run.fix_status !== 'none' && run.fix_status === 'awaiting_approval' && tokenRequired && !authed && (
        <section className="card" aria-label="operator sign-in">
          <h2>operator sign-in</h2>
          <p className="muted">approving a fix requires operator sign-in — the token stays server-side, never in this page</p>
          <form onSubmit={operatorLogin}>
            <label>operator token<input type="password" value={opPassword} onChange={e => setOpPassword(e.target.value)} autoComplete="current-password" /></label>
            <button disabled={opBusy || !opPassword}>{opBusy ? 'signing in…' : 'sign in'}</button>
          </form>
        </section>
      )}
      {run.fix_status && run.fix_status !== 'none' && (
        <section className="card fix" aria-label="proposed fix">
          <h2>ai proposed fix</h2>
          <p className="muted">status: {run.fix_status}{run.fix_branch ? ` · branch ${run.fix_branch}` : ''}{authed && tokenRequired ? <> · operator signed in (<button onClick={operatorLogout}>sign out</button>)</> : ''}</p>
          {run.fix_explanation && <p><b>why</b> — {run.fix_explanation}</p>}
          {run.fix_diff && <pre className="diff">{run.fix_diff}</pre>}
          <p className="muted">verification: pytest, then browser re-test of the original objective</p>
          {run.fix_status === 'awaiting_approval' && (
            <div className="fix-actions">
              <button onClick={() => decideFix('reject')} disabled={fixBusy}>reject</button>
              <button onClick={() => decideFix('approve')} disabled={fixBusy}>{fixBusy ? 'working…' : 'approve fix'}</button>
            </div>
          )}
          {fixError && <p role="alert">{fixError}</p>}
          {run.fix_verify_summary && <p>{run.fix_verify_summary}</p>}
        </section>
      )}
      {run.report && <section className="card"><h2>report</h2><pre>{run.report}</pre></section>}
    </>}
  </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
