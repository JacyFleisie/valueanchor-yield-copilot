'use client'

import { useCallback, useEffect, useState } from 'react'
import { ArrowUpRight, Database, Plug, RefreshCw, Server, ShieldCheck } from 'lucide-react'

// Exactly what POST /mcp returns for a tools/list, and what GET
// /api/openedge/status returns. Nothing is invented here: if a field is missing
// the screen says so rather than filling it in.

type McpTool = {
  name:string
  title?:string
  description:string
  annotations?:{ readOnlyHint?:boolean; destructiveHint?:boolean; idempotentHint?:boolean; openWorldHint?:boolean }
  inputSchema?:{ required?:string[] }
}

type Outbound = {
  key:string
  name:string
  configured:boolean
  url:string
  envUrl:string
  envToken:string
  authHeader:string
  transport:string
  install:string
  docs:string
  notes:string[]
  reachable:boolean|null
  error:string
  tools:string[]
  protocol?:string
  protocolVersion?:string
  serverInfo?:{ name?:string; version?:string }
  supportedVersions?:string[]
}

type OpenEdgeStatus = {
  inbound:{
    name:string
    available:boolean
    endpoint:string
    transport:string
    protocols:string[]
    needs:string
    clientConfig:Record<string, unknown>
    notes:string[]
  }
  outbound:Outbound[]
  checkedAt:string
}

// What GET /api/openedge/link/status returns. Every one of these fields can be
// empty or absent, because on a machine without the kit they are: the card
// renders each blocker in the owner's own words rather than a single greyed-out
// button that says nothing about what to install.
type LinkStatus = {
  available:boolean
  prowin:string
  program:string
  database:string
  blockers:string[]
  env:{ OPENEDGE_PROWIN:string; OPENEDGE_HOME:string; OPENEDGE_DB:string }
}

// What POST /api/openedge/link returns. The figures are read back off the
// response rather than assumed, so the card reports what the database actually
// held - including the case where it held no rows, which comes back as a 400
// with a reason and is shown verbatim.
type LinkResult = {
  business:{ businessId:string; businessName:string }
  accepted:number
  unitsLeft:number|null
  readOnly:boolean
  database:string
  activeBusiness:{ name:string; floorPct:number; floorSource:string; lines:number; note:string }
  note:string
}

// The three blockers the bridge can report, each with its own fix. This is the
// difference between "unavailable" and "here is what to do": an owner who has
// not installed the kit and an owner whose database was never seeded need
// different sentences, and a single greyed button would leave them guessing.
const BLOCKER_TEXT: Record<string,string> = {
  no_avm: 'No AVM found. Install the OpenEdge Developers Kit, or set OPENEDGE_PROWIN to the full path of prowin.exe.',
  no_program: '06-enquiry-json.p was not found beside the backend. The bridge runs that one program and no other.',
  no_database: "No coffee database was found. Create it with 'prodb coffee empty', then run 03-coffee-ddl.sql and 04-seed-coffee.p.",
}

export default function OpenEdgeTab({ api }:{ api:string }) {
  const [status, setStatus] = useState<OpenEdgeStatus | null>(null)
  const [tools, setTools] = useState<McpTool[]>([])
  const [checking, setChecking] = useState(false)
  const [problem, setProblem] = useState('')
  // The local read-only bridge, kept separate from the outbound probe above:
  // this one needs no installed server, so it is the path that actually works
  // on the machine you are sitting at.
  const [linkStatus, setLinkStatus] = useState<LinkStatus | null>(null)
  const [linking, setLinking] = useState(false)
  const [linkResult, setLinkResult] = useState<LinkResult | null>(null)
  const [linkProblem, setLinkProblem] = useState('')

  const loadStatus = useCallback(async (probe:boolean) => {
    setChecking(true)
    try {
      const response = await fetch(`${api}/api/openedge/status${probe ? '?probe=true' : ''}`)
      if (!response.ok) throw new Error('status unavailable')
      setStatus(await response.json())
      setProblem('')
    } catch {
      setStatus(null)
      setProblem(`The backend on ${api} is not answering, so nothing here can be reported as live.`)
    } finally {
      setChecking(false)
    }
  }, [api])

  // The tool list is read out of the MCP endpoint itself, the same way an outside
  // agent reads it, so this screen shows the surface that actually answers rather
  // than a hard-coded copy of it that could go stale.
  const loadTools = useCallback(async () => {
    try {
      const response = await fetch(`${api}/mcp`, {
        method:'POST',
        headers:{ 'Content-Type':'application/json' },
        body:JSON.stringify({ jsonrpc:'2.0', id:1, method:'tools/list' }),
      })
      if (!response.ok) throw new Error('mcp unavailable')
      const body = await response.json()
      setTools(body?.result?.tools ?? [])
    } catch {
      setTools([])
    }
  }, [api])

  useEffect(() => { loadStatus(false); loadTools() }, [loadStatus, loadTools])

  // What the local bridge can see, read once on mount. This is not a probe of
  // somebody else's server - it looks at this machine for the Developers Kit and
  // for the coffee database the starter kit builds, so it is safe to run without
  // asking and it does not wait on anything.
  const loadLinkStatus = useCallback(async () => {
    try {
      const response = await fetch(`${api}/api/openedge/link/status`)
      if (!response.ok) throw new Error('link status unavailable')
      setLinkStatus(await response.json())
    } catch {
      setLinkStatus(null)
    }
  }, [api])

  useEffect(() => { loadLinkStatus() }, [loadLinkStatus])

  const link = useCallback(async () => {
    setLinking(true); setLinkProblem(''); setLinkResult(null)
    try {
      const response = await fetch(`${api}/api/openedge/link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ database: 'coffee' }),
      })
      const body = await response.json().catch(() => null)
      if (!response.ok) {
        // A refusal is a sentence with a fix in it - no AVM, an unseeded
        // database, a database somebody else is serving - so it is shown
        // verbatim rather than replaced with a generic failure.
        setLinkProblem(typeof body?.detail === 'string' ? body.detail : `The link was refused (HTTP ${response.status}).`)
        return
      }
      setLinkResult(body)
    } catch {
      setLinkProblem(`The backend on ${api} is not answering, so the database was not read.`)
    } finally {
      setLinking(false)
    }
  }, [api])

  const inbound = status?.inbound
  const snippet = inbound?.clientConfig ? JSON.stringify(inbound.clientConfig, null, 2) : ''

  return <div className="tab-panel">
    <p className="eyebrow accent">OPENEDGE + MCP</p>
    <h2>The engine is the tool. The model only reads it.</h2>
    <p>ValueAnchor is an MCP server other tools can call today. Reading a business&rsquo;s own OpenEdge records is the other direction, and it needs software you install first.</p>

    {problem && <p className="oe-problem">{problem}</p>}

    <section className="oe-card">
      <div className="oe-head">
        <div className="oe-mark"><Server size={16}/></div>
        <div>
          <b>{inbound?.name ?? 'ValueAnchor as an MCP server'}</b>
          <small>{inbound?.transport ?? 'Asking the backend what it serves…'}</small>
        </div>
        <span className={`oe-state ${inbound?.available ? 'up' : 'off'}`} title={inbound?.available ? 'This endpoint answered on this machine, so the tools below are the ones it actually served - read live rather than remembered.' : inbound ? 'The backend is up but this endpoint did not answer, so nothing below can be claimed as live.' : 'Still waiting to read it from the backend.'}>
          {inbound?.available ? 'live now' : inbound ? 'not answering' : 'unknown'}
        </span>
      </div>
      <div className="oe-facts">
        <div><small>ENDPOINT</small><code>{inbound?.endpoint ?? `${api}/mcp`}</code></div>
        <div><small>REVISIONS SERVED</small><span>{inbound?.protocols?.join(' · ') ?? '—'}</span></div>
      </div>
      <p className="oe-note">{inbound?.needs ?? 'Anything installed on the OpenEdge side is optional for this direction: the surface is the backend itself.'}</p>
      {snippet && <pre className="oe-snippet">{snippet}</pre>}
      <p className="oe-note">Point a host at that config and it gets the yield engine as tools. Every figure it reads was computed here, which is the point: a model that is asked what to bundle calls a tool instead of multiplying a price by a percentage.</p>
    </section>

    <details className="fold">
      <summary>What an agent can call{tools.length ? ` (${tools.length})` : ''}</summary>
      <div className="fold-body">
      <div className="oe-tools-head">
        <p className="eyebrow">READ LIVE FROM {api}/mcp</p>
        <span className="oe-live">{tools.length ? `${tools.length} tools answered` : 'no answer'}</span>
      </div>
      {tools.length ? tools.map(tool => <article className="oe-tool" key={tool.name}>
        <div className="oe-tool-head">
          <code>{tool.name}</code>
          <span className={`oe-tag ${tool.annotations?.readOnlyHint === false ? 'write' : 'read'}`} title={tool.annotations?.readOnlyHint === false ? 'The one tool that writes: it is called after the owner has approved a decision, and it records that decision. It never invents the figures it logs.' : 'Reads this workspace and returns what it found. It changes nothing - safe for an agent to call on its own.'}>
            {tool.annotations?.readOnlyHint === false ? 'changes the record' : 'reads only'}
          </span>
        </div>
        <b>{tool.title ?? tool.name}</b>
        <p>{tool.description}</p>
        <small>takes {(tool.inputSchema?.required ?? []).join(', ') || 'no arguments'}</small>
      </article>) : <p className="modal-note">The MCP endpoint did not answer, so no tool list is shown - a remembered list would be a claim about a surface this machine cannot currently reach.</p>}
      </div>
    </details>

    {/* The local read-only bridge: the path that works on this machine today.
        It is deliberately separate from the outbound direction below, which needs
        software an owner installs. This one needs nothing but the Developers Kit,
        and when the kit is missing it says which of the three things is absent -
        the AVM, the program or the database - because those have three different
        fixes and a single greyed button would leave the owner guessing. */}
    <div className="oe-link-block">
      <div className="oe-tools-head">
        <p className="eyebrow">LINK THIS DATABASE</p>
        <span className="oe-live">
          {linkStatus
            ? linkStatus.available ? 'ready on this machine' : `${linkStatus.blockers.length} thing${linkStatus.blockers.length === 1 ? '' : 's'} to fix first`
            : 'checking this machine…'}
        </span>
      </div>

      <p className="oe-note">
        This is how stock gets in now: not a spreadsheet and not a form, but the rows your own
        OpenEdge database already holds. Pressing Link runs one ABL program,
        <code> 06-enquiry-json.p</code>, whose only statement over data is a <code>FOR EACH</code>
        with <code>NO-LOCK</code> - so it reads and writes nothing back. The rows arrive as the
        business the copilot answers about, and relinking replaces the copy rather than adding to it.
      </p>

      {linkStatus && linkStatus.blockers.length > 0 && <ul className="oe-blockers">
        {linkStatus.blockers.map(blocker => <li key={blocker}>{BLOCKER_TEXT[blocker] ?? blocker}</li>)}
      </ul>}

      <div className="oe-actions">
        <button className="primary-action" onClick={link} disabled={linking || !linkStatus?.available}>
          <Database size={15}/> {linking ? 'Reading the database…' : linkResult ? 'Read it again' : 'Link this database'}
        </button>
      </div>

      {linkProblem && <p className="oe-problem">{linkProblem}</p>}

      {linkResult && <div className="oe-link-result">
        <div className="oe-facts">
          <div><small>BUSINESS</small><b>{linkResult.business.businessName}</b></div>
          <div><small>LINES READ</small><b>{linkResult.accepted}</b></div>
          <div><small>UNITS LEFT</small><b>{linkResult.unitsLeft ?? '—'}</b></div>
          <div><small>ACTIVE FLOOR</small><b>{linkResult.activeBusiness.floorPct}%</b></div>
        </div>
        <p className="oe-note">{linkResult.note}</p>
        <p className="oe-note">Floor source: {linkResult.activeBusiness.floorSource}.</p>
      </div>}

      <p className="modal-note">
        Nothing here can write. The database is opened by name, not by path, so it cannot be turned
        into an address that reaches elsewhere, and the only program this button runs is the read-only
        enquiry above. What it hands over is a mapped copy: your database stays the source of truth,
        and the figures the copilot reasons about are the ones it just read.
      </p>
    </div>

    <details className="fold">
      <summary>Outbound connections (optional)</summary>
      <div className="fold-body">
      <div className="oe-tools-head">
        <p className="eyebrow">THE OUTBOUND DIRECTION</p>
        <button className="guardrail-ghost" title="Asks each configured address for its tool list. Opt-in, so opening this tab never waits on somebody else's server." onClick={() => loadStatus(true)} disabled={checking}>
          <RefreshCw size={12} className={checking ? 'spin' : ''}/> {checking ? 'Checking…' : 'Check now'}
        </button>
      </div>
      {(status?.outbound ?? []).map(entry => <section className={`oe-card${entry.reachable ? ' up' : ''}`} key={entry.key}>
        <div className="oe-head">
          <div className="oe-mark"><Plug size={16}/></div>
          <div>
            <b>{entry.name}</b>
            <small>{entry.transport}</small>
          </div>
          <span className={`oe-state ${entry.reachable === true ? 'up' : entry.reachable === false ? 'down' : 'off'}`} title={entry.reachable === true ? 'Answered the handshake: the tools it advertises are listed below.' : entry.reachable === false ? 'Asked, and it did not answer. The status code and reason are below.' : 'Nothing has been asked of it. Set the variable shown below and press Check now.'}>
            {entry.reachable === true ? 'answering' : entry.reachable === false ? 'not answering' : 'not called'}
          </span>
        </div>
        <div className="oe-facts">
          <div><small>ADDRESS</small><code>{entry.url || '—'}</code></div>
          <div><small>SET THIS</small><code>{entry.envUrl}</code></div>
          <div><small>CREDENTIAL</small><code>{entry.envToken}</code><span>sent as {entry.authHeader}; never returned, logged or stored</span></div>
        </div>
        {entry.error && <p className="oe-note">{entry.error}</p>}
        {!!entry.tools.length && <p className="oe-note">Advertises {entry.tools.length} tool{entry.tools.length === 1 ? '' : 's'}: {entry.tools.slice(0, 8).join(', ')}{entry.tools.length > 8 ? `, and ${entry.tools.length - 8} more` : ''}.</p>}
        {entry.serverInfo?.name && <p className="oe-note">Answered as {entry.serverInfo.name} {entry.serverInfo.version ?? ''} over the {entry.protocol} path, revision {entry.protocolVersion ?? 'unspecified'}.</p>}
        <p className="oe-note">{entry.install}</p>
        <div className="oe-actions">
          <a className="oe-link" href={entry.docs} target="_blank" rel="noreferrer">Documentation <ArrowUpRight size={12}/></a>
        </div>
        <ul className="oe-notes">{entry.notes.map(note => <li key={note}><ShieldCheck size={12}/><span>{note}</span></li>)}</ul>
      </section>)}
      {!status && !problem && <p className="modal-note">Reading the status of both directions…</p>}
      </div>
    </details>

    <p className="modal-note">Nothing here claims a connection it does not have: an address that is not set says so, and a check that failed shows what it said. A probe is only sent when you press Check now.</p>
  </div>
}
