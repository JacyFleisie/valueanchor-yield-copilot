'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, ChevronDown, ChevronUp, Gauge, Loader2, Plug, Plus, RefreshCw, Trash2, Zap } from 'lucide-react'

// See page.tsx: relative when served by the desktop app, :8000 under `next dev`.
const API = process.env.NEXT_PUBLIC_API_URL ?? (typeof window !== 'undefined' && window.location.port !== '3000' ? '' : 'http://localhost:8000')

type Health = {
  LastStatus?: string | null
  LastDetail?: string | null
  LimitValue?: number | null
  Remaining?: number | null
  ResetAt?: string | null
  AvailableAt?: string | null
  LimitSource?: string | null
  HumanRemaining?: string | null
  secondsRemaining?: number
  availableNow?: boolean
  humanRemaining?: string
  Successes?: number
  Failures?: number
}

type ModelEntry = {
  ModelRowID: number
  ModelId: string
  Label: string | null
  Provider: string
  baseUrl: string
  keyEnv: string
  hasKey: boolean
  enabled: boolean
  usableNow: boolean
  blockedBy: string | null
  blockedBySharedLimit: boolean
  Notes: string | null
  health: Health | null
}

type Provider = { slug: string; name: string; baseUrl: string; keyEnv: string; note: string }

type SharedLimit = {
  limit: number | null
  remaining: number | null
  resetAt: string | null
  availableAt: string | null
  secondsRemaining: number
  humanRemaining: string
  limitSource: string | null
  remedy: string | null
  provider: string | null
  scope: string
  explanation: string
}

type Payload = {
  models: ModelEntry[]
  providers: Provider[]
  sharedLimit: SharedLimit | null
  defaults: { provider: string; baseUrl: string }
  howItWorks: string
}

type ProbeResult = {
  probe: { ok: boolean; model?: string; status: string; detail?: string; reply?: string; limitSource?: string }
  rateLimit: { limit: number | null; remaining: number | null; humanRemaining: string; resetAt: string | null; limitSource: string | null }
  sharedLimit: SharedLimit | null
}

function countdown(seconds: number): string {
  if (seconds <= 0) return 'now'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m ${s}s`
  if (m) return `${m}m ${s}s`
  return `${s}s`
}

// Where a model runs is the only choice that has to be made: the provider, the
// endpoint and the key variable all follow from it. Typing those three by hand
// let a model be saved pointing at one server with another server's key.
const RUNTIMES = [
  { slug: 'openrouter', name: 'OpenRouter (free models)', provider: 'openrouter', baseUrl: '' },
  { slug: 'ollama', name: 'Ollama on this machine', provider: 'openai-compatible', baseUrl: 'http://localhost:11434/v1' },
  { slug: 'lmstudio', name: 'LM Studio on this machine', provider: 'openai-compatible', baseUrl: 'http://localhost:1234/v1' },
  { slug: 'llamacpp', name: 'llama.cpp server', provider: 'openai-compatible', baseUrl: 'http://localhost:8080/v1' },
  { slug: 'vllm', name: 'vLLM server', provider: 'openai-compatible', baseUrl: 'http://localhost:8000/v1' },
  { slug: 'custom', name: 'Another OpenAI-compatible server', provider: 'openai-compatible', baseUrl: '' },
]

// The free models this build ships with. Offered as picks so adding one is a
// click; anything outside the list is still reachable through the last option.
const OPENROUTER_PICKS = [
  'nvidia/nemotron-3-super-120b-a12b:free',
  'liquid/lfm-2.5-2.6b:free',
  'nvidia/nemotron-3-ultra-550b-a55b:free',
  'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
]
const CUSTOM_MODEL = '__custom'

export default function ModelsTab() {
  const [data, setData] = useState<Payload | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState<string>('')
  const [probing, setProbing] = useState<Record<number, ProbeResult | null>>({})
  // A ticking clock so the countdowns move without refetching. The server owns
  // the real reset time; this only renders it second by second.
  const [tick, setTick] = useState(0)

  const [runtime, setRuntime] = useState('openrouter')
  const [modelChoice, setModelChoice] = useState(OPENROUTER_PICKS[0])
  const [customModel, setCustomModel] = useState('')
  const [customBaseUrl, setCustomBaseUrl] = useState('')
  const [showAdd, setShowAdd] = useState(false)

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/models`)
      if (!response.ok) throw new Error('models unavailable')
      setData(await response.json())
      setNotice('')
    } catch {
      setNotice('Cannot reach the backend, so the model list is unavailable.')
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const timer = setInterval(() => setTick(value => value + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  // Recomputed from the stored reset time each tick, so it stays truthful even
  // if the page has been open for an hour.
  const liveRemaining = useMemo(() => {
    if (!data?.sharedLimit?.availableAt) return 0
    const reset = new Date(data.sharedLimit.availableAt).getTime()
    if (Number.isNaN(reset)) return 0
    return Math.max(0, Math.round((reset - Date.now()) / 1000))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, tick])

  const call = async (path: string, method: string, body?: unknown, key = '') => {
    setBusy(key)
    try {
      const response = await fetch(`${API}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const payload = await response.json()
      if (!response.ok) setNotice(payload?.detail || 'That did not work.')
      else if (payload.models) setData(current => (current ? { ...current, models: payload.models } : current))
      else await load()
      return payload
    } catch {
      setNotice('The backend did not accept that change.')
      return null
    } finally {
      setBusy('')
    }
  }

  const probe = async (entry: ModelEntry) => {
    setBusy(`probe-${entry.ModelRowID}`)
    try {
      const response = await fetch(`${API}/api/models/probe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modelId: entry.ModelId, provider: entry.Provider, baseUrl: entry.baseUrl, keyEnv: entry.keyEnv }),
      })
      const result: ProbeResult = await response.json()
      setProbing(current => ({ ...current, [entry.ModelRowID]: result }))
      await load()
    } catch {
      setNotice('The probe could not reach the provider.')
    } finally {
      setBusy('')
    }
  }

  const move = (entry: ModelEntry, direction: -1 | 1) => {
    if (!data) return
    const ids = data.models.map(model => model.ModelRowID)
    const index = ids.indexOf(entry.ModelRowID)
    const target = index + direction
    if (target < 0 || target >= ids.length) return
    const reordered = [...ids]
    ;[reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    call('/api/models/order', 'POST', { order: reordered }, 'order')
  }

  const add = async () => {
    const chosen = RUNTIMES.find(entry => entry.slug === runtime) ?? RUNTIMES[0]
    const modelId = (modelChoice === CUSTOM_MODEL ? customModel : modelChoice).trim()
    if (!modelId) { setNotice('Pick a model first.'); return }
    const baseUrl = runtime === 'custom' ? customBaseUrl.trim() : chosen.baseUrl
    if (runtime === 'custom' && !baseUrl) {
      setNotice('Give the server address, for example http://localhost:8000/v1')
      return
    }
    // The key variable comes from the provider catalogue rather than a field, so
    // it always names a variable the server actually reads.
    const keyEnv = data?.providers.find(provider => provider.slug === chosen.provider)?.keyEnv
    const result = await call('/api/models', 'POST', {
      modelId,
      provider: chosen.provider,
      baseUrl: baseUrl || undefined,
      keyEnv: keyEnv || undefined,
    }, 'add')
    if (result?.added) {
      setCustomModel('')
      setCustomBaseUrl('')
      setShowAdd(false)
      setNotice('')
    }
  }

  // Models this build already knows about for the chosen runtime, so the list is
  // never empty when something has been added before.
  const chosenRuntime = RUNTIMES.find(entry => entry.slug === runtime) ?? RUNTIMES[0]
  const modelOptions = Array.from(new Set([
    ...(runtime === 'openrouter' ? OPENROUTER_PICKS : []),
    ...(data?.models ?? []).filter(model => model.Provider === chosenRuntime.provider).map(model => model.ModelId),
  ]))

  if (!data) {
    return (
      <div className="tab-panel">
        <p className="eyebrow accent">LANGUAGE MODELS</p>
        <h2>{notice || 'Reading the model list…'}</h2>
      </div>
    )
  }

  const shared = data.sharedLimit
  const exhausted = Boolean(shared && shared.remaining === 0 && liveRemaining > 0)
  const ready = data.models.filter(model => model.usableNow).length

  return (
    <div className="tab-panel">
      <p className="eyebrow accent">LANGUAGE MODELS</p>
      <h2>
        {data.models.length} model{data.models.length === 1 ? '' : 's'} configured, {ready} ready to answer right now
      </h2>
      <p>{data.howItWorks}</p>

      {exhausted && shared && (
        <div className="quota-banner">
          <AlertTriangle size={15} />
          <div>
            <b>The free daily budget is spent — every model comes back in {countdown(liveRemaining)}</b>
            <span>
              {shared.limit ? `${shared.limit} free requests a day` : 'A daily free allowance'} is counted for the whole
              account, not per model. Switching models does not add any, so the copilot is answering from the local
              model alone until then. {shared.remedy || ''}
            </span>
          </div>
        </div>
      )}

      {!exhausted && shared && shared.limit !== null && (shared.remaining ?? 0) > 0 && (
        <div className="quota-banner soft">
          <Gauge size={15} />
          <div>
            <b>{shared.remaining} of {shared.limit} free requests left today</b>
            <span>Shared across every model on this provider, resetting in {countdown(liveRemaining)}.</span>
          </div>
        </div>
      )}

      {notice && <div className="notice" style={{ marginTop: 14 }}>{notice}</div>}

      <div className="models-list">
        {data.models.map((entry, index) => {
          const health = entry.health
          const probeResult = probing[entry.ModelRowID]
          const perModel = health?.secondsRemaining ?? 0
          return (
            <article className={`model-card${entry.usableNow ? '' : ' blocked'}`} key={entry.ModelRowID}>
              <div className="model-card-head">
                <span className="model-order">{index + 1}</span>
                <div className="model-card-title">
                  <b>{entry.Label || entry.ModelId}</b>
                  <code>{entry.ModelId}</code>
                </div>
                <span className={`model-state ${entry.usableNow ? 'on' : 'off'}`}>
                  {entry.usableNow ? <><Check size={10} /> ready</> : entry.enabled ? <><AlertTriangle size={10} /> blocked</> : 'off'}
                </span>
              </div>

              <div className="model-meta">
                <div><small>PROVIDER</small><span>{entry.Provider}</span></div>
                <div><small>KEY FROM</small><span className={entry.hasKey ? '' : 'missing'}>{entry.keyEnv}{entry.hasKey ? '' : ' — not set'}</span></div>
                <div><small>ENDPOINT</small><span className="mono">{entry.baseUrl || '—'}</span></div>
                <div>
                  <small>LAST RESULT</small>
                  <span>
                    {health?.LastStatus ?? 'never called'}
                    {health?.Failures ? ` (${health.Successes ?? 0} ok / ${health.Failures} failed)` : ''}
                  </span>
                </div>
              </div>

              {entry.blockedBy && <p className="model-blocked">{entry.blockedBy}</p>}

              {perModel > 0 && !entry.blockedBySharedLimit && (
                <p className="model-back">
                  <RefreshCw size={11} /> Out of quota on its own last call. Usable again in <b>{countdown(perModel)}</b>
                  {health?.ResetAt ? ` — resets ${new Date(health.ResetAt).toLocaleString('en-GB', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })}` : ''}.
                </p>
              )}

              {probeResult && (
                <p className={`probe-line ${probeResult.probe.ok ? 'ok' : 'bad'}`}>
                  <Zap size={11} />
                  {probeResult.probe.ok
                    ? <>Replied: “{probeResult.probe.reply || 'ok'}”.</>
                    : <>Probe: {probeResult.probe.status}{probeResult.probe.detail ? ` — ${probeResult.probe.detail.slice(0, 120)}` : ''}.</>}
                  {!probeResult.probe.ok && probeResult.rateLimit.humanRemaining !== 'now' && (
                    <> Usable again in <b>{probeResult.rateLimit.humanRemaining}</b>.</>
                  )}
                </p>
              )}

              <div className="model-actions">
                <button className="ghost" title="Sends one short test prompt to this model and shows whether it answered, what it replied, and how long you are rate-limited for before trying again." disabled={busy === `probe-${entry.ModelRowID}`} onClick={() => probe(entry)}>
                  {busy === `probe-${entry.ModelRowID}` ? <Loader2 size={12} className="spin" /> : <Plug size={12} />} Test it
                </button>
                <button className="ghost" title={`Moves ${entry.Label} earlier in the order the assistant tries them - the top one is asked first.`} disabled={busy === 'order'} onClick={() => move(entry, -1)} aria-label={`Move ${entry.Label} up`}><ChevronUp size={12} /></button>
                <button className="ghost" title={`Moves ${entry.Label} later in the order the assistant tries them.`} disabled={busy === 'order'} onClick={() => move(entry, 1)} aria-label={`Move ${entry.Label} down`}><ChevronDown size={12} /></button>
                <button
                  className={`ghost${entry.enabled ? '' : ' on'}`}
                  title="Keeps this model in the order the assistant tries, or takes it out of the rotation."
                  disabled={busy === `toggle-${entry.ModelRowID}`}
                  onClick={() => call('/api/models/update', 'POST', { rowId: entry.ModelRowID, enabled: !entry.enabled }, `toggle-${entry.ModelRowID}`)}
                >
                  {entry.enabled ? 'Switch off' : 'Switch on'}
                </button>
                <button
                  className="ghost danger"
                  title="Removes this model from the rotation. It is a list on this machine, not an account."
                  disabled={busy === `del-${entry.ModelRowID}`}
                  onClick={() => call(`/api/models/${entry.ModelRowID}`, 'DELETE', undefined, `del-${entry.ModelRowID}`)}
                >
                  <Trash2 size={12} /> Remove
                </button>
              </div>
            </article>
          )
        })}
      </div>

      {showAdd ? (
        <div className="model-add">
          <p className="eyebrow">ADD A MODEL</p>
          <p className="model-add-note">
            Pick where it runs and which model. The endpoint and the key variable are set by that choice, so a model
            is never saved pointing at one server with another server&apos;s key. Keys are not typed here at all: the
            entry names the environment variable the server already holds.
          </p>
          <div className="model-form">
            <label>
              <small>WHERE IT RUNS</small>
              <select
                value={runtime}
                onChange={event => {
                  const next = event.target.value
                  const nextRuntime = RUNTIMES.find(entry => entry.slug === next) ?? RUNTIMES[0]
                  // The model list belongs to the runtime, so a pick from the old
                  // one would otherwise be saved against a server that cannot serve it.
                  const nextPicks = Array.from(new Set([
                    ...(next === 'openrouter' ? OPENROUTER_PICKS : []),
                    ...data.models.filter(model => model.Provider === nextRuntime.provider).map(model => model.ModelId),
                  ]))
                  setRuntime(next)
                  setModelChoice(nextPicks[0] ?? CUSTOM_MODEL)
                }}
              >
                {RUNTIMES.map(entry => <option key={entry.slug} value={entry.slug}>{entry.name}</option>)}
              </select>
            </label>
            <label>
              <small>MODEL</small>
              <select value={modelChoice} onChange={event => setModelChoice(event.target.value)}>
                {modelOptions.map(id => <option key={id} value={id}>{id}</option>)}
                <option value={CUSTOM_MODEL}>
                  {modelOptions.length === 0 ? 'Type the model’s name' : 'Something not listed…'}
                </option>
              </select>
            </label>
            {modelChoice === CUSTOM_MODEL && (
              <label>
                <small>MODEL NAME</small>
                <input
                  value={customModel}
                  placeholder={runtime === 'openrouter' ? 'vendor/model:free' : 'llama3.2:3b'}
                  onChange={event => setCustomModel(event.target.value)}
                />
              </label>
            )}
            {runtime === 'custom' && (
              <label>
                <small>SERVER ADDRESS</small>
                <input
                  value={customBaseUrl}
                  placeholder="http://localhost:8000/v1"
                  onChange={event => setCustomBaseUrl(event.target.value)}
                />
              </label>
            )}
          </div>
          <div className="model-add-actions">
            <button className="primary-action" disabled={busy === 'add'} onClick={add}>
              {busy === 'add' ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Add it to the chain
            </button>
            <button className="ghost" onClick={() => { setShowAdd(false); setNotice('') }}>Cancel</button>
          </div>
          <p className="modal-note">
            {data.providers.find(p => p.slug === chosenRuntime.provider)?.note}{' '}
            {chosenRuntime.baseUrl ? `Calls go to ${chosenRuntime.baseUrl}.` : 'Calls go to the address you type.'}
          </p>
        </div>
      ) : (
        <button className="primary-action" onClick={() => setShowAdd(true)}><Plus size={13} /> Add a model</button>
      )}

      <div className="panel" style={{ marginTop: 20 }}>
        <p className="eyebrow">HOW THE CHAIN BEHAVES</p>
        <ol>
          <li>Models are tried top to bottom. The first one that answers with valid JSON and passing figures is used.</li>
          <li>A model already out of quota is skipped rather than called, so you are not made to wait for a refusal.</li>
          <li>Whatever answers, every figure is still traced back to your live data before you see it.</li>
          <li>If nothing answers, the local model does — you keep the answer, and it says so.</li>
        </ol>
      </div>
    </div>
  )
}
