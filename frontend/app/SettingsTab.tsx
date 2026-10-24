'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, ShieldCheck } from 'lucide-react'
import ModelsTab from './ModelsTab'

// Both settings are picks rather than typed values: a floor is a policy choice,
// and a currency code already knows its symbol - asking for both invited a code
// and a symbol that disagreed with each other.
const FLOOR_STEPS = [40, 45, 50, 55, 60, 65, 70, 75, 80]
const CURRENCIES = [
  { code: 'ZAR', symbol: 'R' },
  { code: 'USD', symbol: '$' },
  { code: 'EUR', symbol: '€' },
  { code: 'GBP', symbol: '£' },
  { code: 'KES', symbol: 'KSh' },
  { code: 'NGN', symbol: '₦' },
  { code: 'AUD', symbol: 'A$' },
  { code: 'CAD', symbol: 'C$' },
  { code: 'INR', symbol: '₹' },
  { code: 'AED', symbol: 'AED' },
]

// Exactly what GET /api/profile returns - the same object the yield engine and
// the copilot read, so this screen edits the real thing rather than a copy.
type Profile = {
  sector: string
  name: string
  businessName: string
  inventoryNoun: string
  marginFloorPct: number
  currencyCode: string
  currencySymbol: string
  floorSource: string | null
  floorOwnerSet: boolean
  connected: boolean
}

export default function SettingsTab({ api, onSaved }: { api: string; onSaved?: () => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [health, setHealth] = useState<'checking' | 'ok' | 'down'>('checking')
  const [problem, setProblem] = useState('')

  // Both editable settings are drafts, so the field shows what the owner picked
  // and only Save reaches the profile every other screen reads.
  const [floorDraft, setFloorDraft] = useState('')
  const [currencyDraft, setCurrencyDraft] = useState({ code: '', symbol: '' })
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    try {
      const [profileRes, healthRes] = await Promise.all([
        fetch(`${api}/api/profile`),
        fetch(`${api}/api/health`),
      ])
      if (!profileRes.ok) throw new Error('profile unavailable')
      setProfile(await profileRes.json())
      setHealth(healthRes.ok ? 'ok' : 'down')
      setProblem('')
    } catch {
      setHealth('down')
      setProblem(`Nothing on this tab can be read: the backend on ${api} is not answering. No setting was changed.`)
    }
  }, [api])

  useEffect(() => { load() }, [load])

  // Refresh the drafts whenever a fresh profile lands, so a save that the server
  // rounded (10.4 -> 10) shows what is actually stored rather than the typo.
  useEffect(() => {
    if (!profile) return
    setFloorDraft(String(profile.marginFloorPct ?? 60))
    setCurrencyDraft({ code: profile.currencyCode ?? 'ZAR', symbol: profile.currencySymbol ?? 'R' })
  }, [profile])

  const floorValid = floorDraft !== '' && Number(floorDraft) >= 10 && Number(floorDraft) <= 95
  const floorDirty = profile ? Number(floorDraft) !== profile.marginFloorPct : false
  const currencyDirty = profile
    ? currencyDraft.code !== profile.currencyCode || currencyDraft.symbol !== profile.currencySymbol
    : false

  // null is a real instruction: go back to the floor this workspace arrived
  // with. The response carries both the new profile and the server's own words
  // for what happened, which are shown verbatim rather than paraphrased.
  const saveFloor = async (value: number | null) => {
    setSaving(true); setNote('')
    try {
      const response = await fetch(`${api}/api/profile/guardrails`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ marginFloorPct: value }),
      })
      if (!response.ok) throw new Error('rejected')
      const body = await response.json()
      setProfile(body.profile)
      setNote(body.note ?? '')
      // The floor and the currency are read by every other screen, so the
      // workspace reloads now rather than showing yesterday's number until the
      // owner happens to refresh.
      onSaved?.()
    } catch {
      setNote('That floor was not saved - the request was refused. Nothing on this page changed.')
    } finally {
      setSaving(false)
    }
  }

  const saveCurrency = async () => {
    setSaving(true); setNote('')
    try {
      const response = await fetch(`${api}/api/profile/currency`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ currencyCode: currencyDraft.code, currencySymbol: currencyDraft.symbol }),
      })
      if (!response.ok) throw new Error('rejected')
      setProfile(await response.json())
      setNote(`Money across the workspace is now reported in ${currencyDraft.code}.`)
      onSaved?.()
    } catch {
      setNote('That currency was not saved - it needs a three-letter ISO code and a symbol of one to three characters.')
    } finally {
      setSaving(false)
    }
  }

  // A profile can hold a value this list does not ship with (an older floor, or
  // a currency added by hand). It is offered as itself rather than silently
  // snapped to a neighbour the owner never chose.
  const floorChoices = Array.from(new Set([...FLOOR_STEPS, profile?.marginFloorPct ?? 60])).sort((a, b) => a - b)
  const currencyChoices = profile && !CURRENCIES.some(currency => currency.code === profile.currencyCode)
    ? [...CURRENCIES, { code: profile.currencyCode, symbol: profile.currencySymbol }]
    : CURRENCIES

  return (
    <div className="tab-panel">
      <p className="eyebrow accent">SETTINGS</p>
      <h2>Your floor, your money, your assistant</h2>
      <p>The floor every answer prices against, the currency money is reported in, and the models that may reword an answer.</p>

      {problem && <div className="notice">{problem}</div>}

      <div className="guardrail-stack">
        <div className="guardrail-row">
          <span>Business</span>
          <strong>{profile ? `${profile.businessName} · ${profile.name}` : 'reading…'}</strong>
        </div>
        <div className="guardrail-row">
          <span>Stock comes from</span>
          <strong>{profile?.connected ? 'Read from OpenEdge (linked on the OpenEdge tab)' : 'Link this database (OpenEdge tab)'}</strong>
        </div>

        <div className="guardrail-row">
          <span>Minimum retained margin</span>
          <div className="guardrail-control">
            <select
              className="guardrail-select floor"
              aria-label="Minimum retained margin, percent"
              title="The margin floor, as a percentage of shelf price. Saving it changes what every bundle, pairing and copilot answer is priced against."
              value={floorDraft}
              disabled={!profile}
              onChange={(event) => setFloorDraft(event.target.value)}
            >
              {floorChoices.map(value => <option key={value} value={value}>{value}%</option>)}
            </select>
            <button
              className="guardrail-save"
              title="Writes your floor onto the profile the engine reads."
              onClick={() => saveFloor(Number(floorDraft))}
              disabled={saving || !floorDirty || !floorValid}
            >
              Save floor
            </button>
            <button
              className="guardrail-ghost"
              title="Clears your own floor and goes back to the one this workspace arrived with."
              onClick={() => saveFloor(null)}
              disabled={saving || !profile?.floorOwnerSet}
            >
              Use the floor it came with
            </button>
          </div>
        </div>

        <div className="guardrail-row">
          <span>Where that floor came from</span>
          <strong>{profile?.floorSource ?? 'reading…'}</strong>
        </div>
        <div className="guardrail-row">
          <span>Discounting allowed</span>
          <strong className="blocked">Never - no markdown path exists</strong>
        </div>

        <div className="guardrail-row">
          <span>Reporting currency</span>
          <div className="guardrail-control">
            <select
              className="guardrail-select currency"
              aria-label="Reporting currency"
              title="The currency money is reported in. Picking a code sets its symbol too, so the two cannot disagree."
              value={currencyDraft.code}
              disabled={!profile}
              onChange={(event) => {
                const chosen = currencyChoices.find(currency => currency.code === event.target.value)
                setCurrencyDraft({ code: event.target.value, symbol: chosen?.symbol ?? currencyDraft.symbol })
              }}
            >
              {currencyChoices.map(currency => <option key={currency.code} value={currency.code}>{currency.code} · {currency.symbol}</option>)}
            </select>
            <button
              className="guardrail-save"
              onClick={saveCurrency}
              disabled={saving || !currencyDirty || currencyDraft.code.length !== 3 || currencyDraft.symbol.length < 1}
            >
              Save currency
            </button>
          </div>
        </div>

        <div className="guardrail-row">
          <span>Backend</span>
          <strong>{health === 'checking' ? 'checking…' : health === 'ok' ? 'answering' : 'not answering'}</strong>
        </div>
      </div>

      {note && <p className="modal-note accent">{note}</p>}
      <p className="modal-note">
        One floor, read by everything: the planner, the pairings, the profit scenarios and the copilot.
        Clearing your override returns to the floor this workspace arrived with.
      </p>

      <div className="model-add-actions">
        <button className="ghost" onClick={load} disabled={saving} title="Re-reads the profile and backend health.">
          <RefreshCw size={14}/> Re-check
        </button>
      </div>

      {/* The assistant registry is a list an owner edits occasionally, not a
          value they check daily, so it stays closed until asked for. */}
      <details className="fold settings-fold">
        <summary>Assistant models</summary>
        <div className="fold-body">
          <ModelsTab />
        </div>
      </details>

      <div className="guardrail-row" style={{ marginTop: '22px' }}>
        <span><ShieldCheck size={14}/> Keys</span>
        <strong>Read from the environment at runtime - never stored in this app</strong>
      </div>
    </div>
  )
}
