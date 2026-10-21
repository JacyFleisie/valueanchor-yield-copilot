'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, ShieldCheck } from 'lucide-react'
import ModelsTab from './ModelsTab'

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

export default function SettingsTab({ api }: { api: string }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [health, setHealth] = useState<'checking' | 'ok' | 'down'>('checking')
  const [problem, setProblem] = useState('')

  // Both editable numbers are drafts, so the field shows what the owner is
  // typing and only Save reaches the profile every other screen reads.
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
    } catch {
      setNote('That currency was not saved - it needs a three-letter ISO code and a symbol of one to three characters.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="tab-panel">
      <p className="eyebrow accent">SETTINGS</p>
      <h2>Owner customisation</h2>
      <p>Everything an owner is allowed to change lives here: the floor every answer prices against, the currency money is reported in, and the assistant chain. Stock itself is not changed from this tab - it arrives through Link this database on the OpenEdge tab, and a second way in would be a second source of truth.</p>

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
            <input
              type="number"
              className="guardrail-input"
              min={10}
              max={95}
              step={5}
              aria-label="Minimum retained margin, percent"
              title="The margin floor, as a percentage of shelf price. Saving it changes what every bundle, pairing and copilot answer is priced against."
              value={floorDraft}
              onChange={(event) => setFloorDraft(event.target.value)}
            />
            <b>%</b>
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
            <input
              type="text"
              className="guardrail-input short"
              maxLength={3}
              aria-label="Currency code"
              title="Three-letter ISO code, e.g. ZAR."
              value={currencyDraft.code}
              onChange={(event) => setCurrencyDraft({ ...currencyDraft, code: event.target.value.toUpperCase() })}
            />
            <input
              type="text"
              className="guardrail-input short"
              maxLength={3}
              aria-label="Currency symbol"
              title="One to three characters, e.g. R."
              value={currencyDraft.symbol}
              onChange={(event) => setCurrencyDraft({ ...currencyDraft, symbol: event.target.value })}
            />
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
        A floor set here is stored on this business&apos;s profile, so the planner, the pairing costs, the profit
        scenarios and the copilot all read the same number rather than a second copy that can fall out of step. The
        floor this workspace arrived with is not thrown away: clearing the override returns to it, and the row above
        says which one is in force.
      </p>

      <div className="model-add-actions">
        <button className="ghost" onClick={load} disabled={saving} title="Re-reads the profile and backend health.">
          <RefreshCw size={14}/> Re-check
        </button>
      </div>

      {/* The assistant registry is the whole of the old Models screen: which
          models are configured, the order they are tried in, and why any of
          them cannot answer right now. It keeps its own panel because it is a
          list an owner edits, not a single value like the rows above. */}
      <div style={{ marginTop: '30px' }}>
        <ModelsTab />
      </div>

      <div className="guardrail-row" style={{ marginTop: '22px' }}>
        <span><ShieldCheck size={14}/> Keys</span>
        <strong>Read from the environment at runtime - never stored in this app</strong>
      </div>
    </div>
  )
}
