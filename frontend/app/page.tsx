'use client'

import { useEffect, useState } from 'react'
import { ArrowUpRight, BarChart3, Bell, ChevronRight, CircleHelp, Clock3, Filter, Menu, PackageCheck, Plug, Search, Settings2, ShieldCheck, Sparkles, X, Zap } from 'lucide-react'
import CopilotChat from './CopilotChat'
import OpenEdgeTab from './OpenEdgeTab'
import SettingsTab from './SettingsTab'

type Inventory = { itemId:number; itemName:string; category:string; sector:string; basePrice:number; hoursRemaining:number; velocityIndex:number; urgencyScore:number; riskStatus:string; unitsAvailable:number; unitsSold:number; totalCapacity:number }
type Profile = { sector:string; name:string; businessName:string; blurb:string; inventoryNoun:string; itemLabel:string; heroLine:string; sampleQuestion:string; marginFloorPct:number; currencyCode:string; currencySymbol:string; floorSource?:string; floorOwnerSet?:boolean; connected?:boolean }
type Perk = { PerkName:string; RetailValue:number; CostToBusiness:number }
type Tier = { tier:number; label:string; status:'funded'|'deferred'|'withheld'; perk:Perk|null }
type ProfitScenario = { key:string; label:string; cashPerUnit:number; floorRespected:boolean; perkCost:number; unitsMoved:number; cash:number; writeOff:number|null; verdict:string; detail:string }
type ProfitBreakdown = {
  unitsLeft:number; markdownPercent:number;
  floor:{ percent:number; perUnit:number; roomToAddValue:number };
  scenarios:ProfitScenario[]; recommended:string;
  breakeven:{ ratio:number; extraPercent:number; direction:string; ifBundled:number; ifMarkedDown:number; explanation:string }|null;
  deferred:{ amount:number; note:string }; notModelled:string
}
type Analysis = { profile:Profile; item:Inventory; metrics:{ marginPreserved:number; marginThreshold:number; deferredCommitment:number; floorRespected:boolean; urgencyScore:number; velocityIndex:number }; recommendation:{ headline:string; action:string; marginRetained:number; tiers:Tier[] }; profit:ProfitBreakdown }
type Decision = { strategyId:number; itemId:number; itemLabel:string; category:string; sector:string; managerDecision:string; unitsFilled:number; marginRetained:number; createdTimestamp:string };
type Tab = 'Overview' | 'Inventory' | 'Copilot AI' | 'OpenEdge' | 'Settings'
type Learning = {
  turns:number; helpful:number; unhelpful:number; confirmedReadings:number; learnedExamples:number;
  learnedPerIntent:Record<string, number>;
  accuracyHistory:{ Sector:string; HoldoutAccuracy:number; TrainingRows:number; LearnedRows:number; CreatedTimestamp:string }[];
  recentQuestions:{ Question:string; Intent:string; ConfirmedIntent:string|null; Rating:number; CreatedTimestamp:string }[];
  howItLearns:string; learningRule:string; sector:string
}
type Knowledge = {
  summary:{ documents:number; byKind:Record<string, number>; glossaryTerms:number; terms:number; builtFrom:string };
  sampleQuestion:string;
  sampleSources:{ id:string; kind:string; title:string; detail:string; relevance:number }[];
  grounding:string
}
type NluModel = {
  name:string; architecture:string; features:number;  training:{ rows:number; learnedRows:number; holdoutRows:number; evaluationSplits:number; seconds:number; regularisationC:number; trainedAt:string; templatesPerIntent:Record<string, number> };
  scores:{ holdoutAccuracy:number; holdoutStdDev:number; holdoutWorst:number; crossValidationAccuracy:number };
  intents:{ intent:string; label:string; description:string }[];
  vocabulary:{ items:number; categories:number; spellingWords:number; englishWords:number };
  components:{ name:string; role:string }[];
  profile:{ sector:string; businessName:string; inventoryNoun:string; marginFloorPct:number };
  sampleUtterances:string[];
  leavesMachine:boolean;
  assistant:{ enabled:boolean; models:string[]; role:string; guardrails:string[] }
}

// Five tabs, down from nine. The two that were cut - Spreadsheet and Strategies
// - were stock-*writing* paths: a sheet you pasted figures into, and a preset
// picker that chose which demo business to seed. Both are gone because stock
// arrives through Link this database on the OpenEdge tab now, and a second way
// in would be a second source of truth. Guardrails moved into Settings and the
// decision log into Copilot AI, where the decisions are actually made, so
// nothing an owner could do became unreachable - it just stopped being a place
// you had to visit to find one control.
const TABS: Tab[] = ['Overview', 'Inventory', 'Copilot AI', 'OpenEdge', 'Settings']
const TAB_ICON: Record<Tab, string> = { Overview:'bars', Inventory:'package', 'Copilot AI':'sparkles', OpenEdge:'plug', Settings:'settings' }

// Clarifying chips do not guess at a rephrasing: they send the original question
// back with the chosen intent, which the engine then takes as confirmed.
const API = process.env.NEXT_PUBLIC_API_URL || (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:8000` : 'http://localhost:8000')
const glyph = (category:string) => /pastry|cake|bake|bread/i.test(category) ? '◒' : /room|suite/i.test(category) ? '⌂' : /produce|dairy|fresh/i.test(category) ? '◍' : /kitchen|deli|savour|coach|transfer/i.test(category) ? '▤' : '↗'

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>('Overview')
  const [profile, setProfile] = useState<Profile | null>(null)
  const [items, setItems] = useState<Inventory[]>([])
  const [history, setHistory] = useState<Decision[]>([])
  const [selected, setSelected] = useState<Inventory | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [onlyAtRisk, setOnlyAtRisk] = useState(false)
  // "Loaded and empty" is a different state from "still loading": without this
  // flag the table says "Loading live inventory..." forever when a workspace has
  // no rows yet, which reads as a hang rather than as the next thing to do.
  const [loaded, setLoaded] = useState(false)
  const [model, setModel] = useState<NluModel | null>(null)
  const [learning, setLearning] = useState<Learning | null>(null)
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null)
  // A question raised from elsewhere on the page - a starter chip, or the model
  // tab - is handed to the one chat rather than opening a second conversation.
  const [queued, setQueued] = useState<{ text:string; nonce:number } | null>(null)
  const queue = (text:string) => setQueued({ text, nonce: Date.now() })
  // The whole product is about what lapses before close, so a stale date in the
  // header would undercut it. Set after mount so the server and client render the
  // same markup and hydration stays quiet.
  const [clock, setClock] = useState<{ day:string; greeting:string } | null>(null)
  useEffect(() => {
    const now = new Date()
    setClock({
      day: now.toLocaleDateString('en-GB', { weekday:'long', day:'numeric', month:'long', year:'numeric' }).toUpperCase(),
      greeting: now.getHours() < 12 ? 'Good morning' : now.getHours() < 18 ? 'Good afternoon' : 'Good evening',
    })
  }, [])

  const money = (value:number) => `${profile?.currencySymbol ?? 'R'}${value.toLocaleString('en-ZA', { maximumFractionDigits: 0 })}`
  const atRisk = items.filter(item => item.riskStatus !== 'healthy')
  const visible = onlyAtRisk ? atRisk : items
  const categories = new Set(items.map(item => item.category)).size
  const unitsAtRisk = atRisk.reduce((total, item) => total + item.unitsAvailable, 0)

  const loadInventory = async () => {
    try {
      const response = await fetch(`${API}/api/inventory`)
      if (!response.ok) throw new Error('inventory unavailable')
      const body = await response.json()
      setProfile(body.profile)
      setItems(body.items)
      setLoaded(true)
      setNotice('')
    } catch {
      setLoaded(true)
      setNotice(`Backend offline. Start FastAPI on port 8000 to load live ${profile?.inventoryNoun ?? 'inventory'}.`)
    }
  }
  const loadHistory = async () => {
    try {
      const response = await fetch(`${API}/api/strategy/history`)
      if (!response.ok) throw new Error('history unavailable')
      const body = await response.json()
      setHistory(body.decisions)
    } catch {
      setHistory([])
    }
  }
  const loadModel = () => fetch(`${API}/api/nlu/model`).then(response => response.json()).then(setModel).catch(() => setModel(null))
  const loadLearning = () => fetch(`${API}/api/learning`).then(response => response.json()).then(setLearning).catch(() => setLearning(null))
  const loadKnowledge = () => fetch(`${API}/api/knowledge`).then(response => response.json()).then(setKnowledge).catch(() => setKnowledge(null))

  useEffect(() => { loadInventory() }, [])
  useEffect(() => { if (activeTab === 'Copilot AI') loadHistory() }, [activeTab])
  useEffect(() => { if (activeTab === 'Copilot AI') { loadModel(); loadLearning(); loadKnowledge() } }, [activeTab])

  const analyze = async (item:Inventory) => {
    setSelected(item); setLoading(true); setAnalysis(null)
    try {
      const response = await fetch(`${API}/api/yield/analyze`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ itemId:item.itemId }) })
      if (!response.ok) throw new Error('analysis failed')
      setAnalysis(await response.json())
    } catch {
      setNotice('Could not reach the analysis engine.')
      setSelected(null)
    } finally {
      setLoading(false)
    }
  }

  const decide = async (managerDecision:'Approved'|'Declined') => {
    if (!analysis) return
    try {
      const response = await fetch(`${API}/api/strategy/decide`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ itemId:analysis.item.itemId, recommendedAction:analysis.recommendation.action, managerDecision, unitsFilled:Math.max(1, Math.round(analysis.item.unitsAvailable * .4)), marginRetained:analysis.recommendation.marginRetained }) })
      if (!response.ok) throw new Error('decision failed')
      setNotice(managerDecision === 'Approved' ? `Logged: ${analysis.item.itemName} holds the ${money(analysis.item.basePrice)} price with value added.` : 'Recommendation declined. No price change made.')
    } catch {
      setNotice('Could not log that decision. The audit trail is unchanged.')
    } finally {
      setSelected(null); setAnalysis(null); loadHistory()
    }
  }

  const noun = profile?.inventoryNoun ?? 'inventory'

  return <main>
    {menuOpen && <button className="rail-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)}/>}
    <aside className={`rail${menuOpen ? ' open' : ''}`}>
      <button className="rail-close" aria-label="Close navigation" onClick={() => setMenuOpen(false)}><X size={18}/></button>
      <div className="brand"><div className="brand-mark">V</div><span>Value<br/><b>Anchor</b></span></div>
      <nav>{TABS.map(tab => <button key={tab} className={activeTab === tab ? 'active' : ''} aria-current={activeTab === tab} onClick={() => { setActiveTab(tab); setMenuOpen(false) }}>{tabIcon(tab)} {tab}</button>)}</nav>
      <div className="rail-bottom"><a><Settings2 size={18}/>Settings</a><a><CircleHelp size={18}/>Support</a><div className="profile"><div className="avatar">AM</div><div><b>Alex Morgan</b><small>Yield manager</small></div></div></div>
    </aside>
    <section className="content">
      <header>
        <button className="mobile-menu" aria-label="Open navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><Menu size={20}/></button>
        <div><p className="eyebrow">{clock?.day ?? '\u00a0'}</p><h1>{clock?.greeting ?? 'Welcome back'}, Alex <span>✦</span></h1></div>
        <div className="header-actions"><button className="icon-button" aria-label="Search"><Search size={18}/></button><button className="icon-button" aria-label="Notifications"><Bell size={18}/><i/></button><div className="avatar">AM</div></div>
      </header>

      {activeTab === 'Overview' && <>
        <div className="hero">
          <div>
            <p className="eyebrow accent">YIELD CONTROL CENTER</p>
            <h2>Protect the price floor.<br/><em>{profile?.heroLine ?? 'Capture the next sale.'}</em></h2>
            <p className="hero-copy">{profile?.connected ? `Running on the stock read from ${profile.businessName},` : `Runs on the ${(profile?.name ?? 'coffee shop').toLowerCase()} preset,`} and on any business<br className="desktop"/> that sells stock or capacity before it lapses. Ask the copilot a question and it answers from the live numbers, with its working shown.</p>
          </div>
          <div className="hero-orb">
            <div className="orb-ring ring-1"/><div className="orb-ring ring-2"/>
            <div className="orb-core"><Zap size={27}/><small>LIVE</small></div>
            <span className="orb-label label-a">MARGIN FLOOR<br/><b>≥{profile?.marginFloorPct ?? 60}%</b></span>
            <span className="orb-label label-b">MARKDOWNS<br/><b>NEVER</b></span>
          </div>
        </div>
        <CopilotChat api={API} business={profile?.businessName ?? 'your business'} sample={profile?.sampleQuestion ?? 'Which batch needs attention first?'}/>
        <div className="metrics">
          <Metric icon={<PackageCheck/>} label={`Active ${noun}`} value={String(items.length).padStart(2,'0')} detail={`across ${categories} ${categories === 1 ? 'category' : 'categories'}`} tone="blue" hint={`Counted from the live ${noun} this workspace holds - the same rows the copilot reads.`}/>
          <Metric icon={<Zap/>} label="At-risk lines" value={String(atRisk.length).padStart(2,'0')} detail="need attention today" tone="orange" hint="The engine's own call: time running out and little of the line sold. Not a forecast - a ranking of what is on the shelf now."/>
          <Metric icon={<ArrowUpRight/>} label="Units at risk" value={String(unitsAtRisk)} detail={`unsold in ${noun}`} tone="purple" hint="Unsold units on those at-risk lines, counted from the shelf. Nothing is estimated."/>
          <Metric icon={<ShieldCheck/>} label="Margin floor" value={`${profile?.marginFloorPct ?? 60}%`} detail="no markdowns below it" tone="green" hint={`What every answer prices against: ${profile?.marginFloorPct ?? 60}% of shelf price. Change it on the Settings tab.`}/>
        </div>
      </>}

      {activeTab === 'OpenEdge' && <OpenEdgeTab api={API} />}

      {activeTab === 'Settings' && <SettingsTab api={API} />}

      {activeTab === 'Copilot AI' && <>
        <div className="tab-panel">
          <p className="eyebrow accent">HOW IT THINKS</p>
          <h2>{model ? `${model.intents.length} ways of understanding a question, and a model that learns yours` : 'Reading the model card…'}</h2>
          <p>A question is read by a small model trained on this machine, then answered from records retrieved out of your live data, and only then put into plain words. The reading is a hybrid of word and character TF-IDF into a logistic regression, with spelling repair and catalogue matching either side of it. Nothing is downloaded. The confidence number is how sure that reading was; the how-it-worked-it-out panel on any answer is what it read from your stock to answer.</p>

          {model && <>
            <div className="model-grid">
              <div className="model-stat"><small>HOLDOUT ACCURACY</small><b>{Math.round(model.scores.holdoutAccuracy * 100)}%<em>±{Math.round(model.scores.holdoutStdDev * 100)}</em></b><span>mean over {model.training.evaluationSplits} splits of {model.training.holdoutRows} unseen sentences, worst {Math.round(model.scores.holdoutWorst * 100)}%</span></div>
              <div className="model-stat"><small>LEARNED FROM YOU</small><b>{model.training.learnedRows}</b><span>confirmed questions of your own now part of training, on top of {model.training.rows} generated phrasings</span></div>
              <div className="model-stat"><small>TRAINING ROWS</small><b>{model.training.rows}</b><span>{model.features.toLocaleString('en-ZA')} features, retrained in {model.training.seconds}s the moment you confirm a reading</span></div>
              <div className="model-stat"><small>WHAT IT KNOWS ABOUT YOU</small><b>{model.vocabulary.items}</b><span>{model.vocabulary.categories} categories and {model.vocabulary.spellingWords} of your own words in the spelling dictionary</span></div>
            </div>

            <div className="assistant-panel">
              <div className="assistant-head">
                <ShieldCheck size={15}/>
                <div>
                  <b>{model.assistant.enabled ? 'Assisted, never in charge' : 'Running fully offline'}</b>
                  <small>{model.assistant.role}</small>
                </div>
                <span className={`assistant-state ${model.assistant.enabled ? 'on' : 'off'}`}>
                  {model.assistant.enabled ? 'assistant connected' : 'no assistant connected'}
                </span>
              </div>
              <div className="guardrail-grid">
                {model.assistant.guardrails.map(rule => <div className="guardrail-item" key={rule}><ShieldCheck size={12}/><span>{rule}</span></div>)}
              </div>
              <p className="meta-note">
                {model.assistant.enabled
                  ? `${model.assistant.models.length} free models are tried in order. Only a rephrasing request ever leaves this machine, and only for a question that is already known to be about this business.`
                  : 'No key is configured, so no request can leave this machine at all. Every answer, figure and refusal still works.'}
              </p>
            </div>

            <div className="model-split">
              <div>
                <p className="eyebrow">WHAT IT UNDERSTANDS</p>
                <div className="intent-list">{model.intents.map(intent => <div className="intent-row" key={intent.intent}>
                  <b>{intent.label}</b><small>{intent.description}</small>
                  <em>{model.training.templatesPerIntent[intent.intent] ?? 0} phrasings</em>
                </div>)}</div>
              </div>
              <div>
                <p className="eyebrow">WHAT IT RETRIEVES</p>
                {knowledge
                  ? <div className="intent-list">
                      <div className="intent-row"><b>{knowledge.summary.documents} records</b><small>{knowledge.summary.builtFrom}</small></div>
                      {Object.entries(knowledge.summary.byKind).map(([kind, count]) => <div className="intent-row" key={kind}>
                        <b>{kind === 'stock' ? 'Your live lines' : kind === 'perk' ? 'Perks you can offer' : kind === 'rule' ? 'Your pricing rules' : kind === 'glossary' ? 'Words explained' : kind === 'decision' ? 'Decisions you logged' : kind === 'sector' ? 'Other business types' : 'Questions you taught it'}</b>
                        <small>{TYPE_NOTE[kind] ?? 'part of the retrievable record'}</small>
                        <em>{count}</em>
                      </div>)}
                    </div>
                  : <p>Reading the index…</p>}
                <p className="eyebrow" style={{ marginTop:18 }}>TRY A SENTENCE</p>
                <div className="phrase-chips">{model.sampleUtterances.map(sentence => <button key={sentence} onClick={() => queue(sentence)}>{sentence}</button>)}</div>
              </div>
            </div>
            <p className="modal-note">Trained {model.training.trainedAt} against the live {model.profile.inventoryNoun} of {model.profile.businessName} ({model.profile.sector}, {model.profile.marginFloorPct}% floor). The holdout score is measured on phrasings the classifier has never seen, because the split happens before the slot expansion - so it is an honest estimate, not a number that flatters itself.</p>
          </>}
        </div>

        <div className="tab-panel learning-panel">
          <p className="eyebrow accent">WHAT IT HAS LEARNED HERE</p>
          <h2>It gets better at your business, not just at English</h2>
          <p>{learning?.howItLearns ?? 'Reading what has been learned…'}</p>
          {learning && <>
            <div className="model-grid">
              <div className="model-stat"><small>QUESTIONS ANSWERED</small><b>{learning.turns}</b><span>every one written down with its answer, its figures and where they came from</span></div>
              <div className="model-stat"><small>CONFIRMED READINGS</small><b>{learning.confirmedReadings}</b><span>times you told it what you actually meant, or marked an answer helpful</span></div>
              <div className="model-stat"><small>NOW IN TRAINING</small><b>{learning.learnedExamples}</b><span>of your own phrasings merged into the model on the next rebuild</span></div>
              <div className="model-stat"><small>MARKED HELPFUL</small><b>{learning.helpful}<em>/{learning.unhelpful} not</em></b><span>thumbs down is recorded but never learned from, so a bad answer cannot be reinforced</span></div>
            </div>

            {learning.accuracyHistory.length > 1 && <div className="trend">
              <p className="eyebrow">ACCURACY ACROSS RETRAINS</p>
              <div className="trend-bars">
                {learning.accuracyHistory.map((point, index) => <div className="trend-bar" key={index}>
                  <i style={{ height:`${Math.max(8, Math.round(point.HoldoutAccuracy * 100))}%` }}/>
                  <small>{Math.round(point.HoldoutAccuracy * 100)}%</small>
                  <span>{point.LearnedRows} learned</span>
                </div>)}
              </div>
              <p className="meta-note">Each bar is one training run, scored on sentences the model had never seen. Confirming a reading makes the next bar a real measurement of whether learning helped on this business.</p>
            </div>}

            {learning.recentQuestions.length > 0 && <>
              <p className="eyebrow" style={{ marginTop:22 }}>WHAT YOU HAVE ASKED</p>
              <div className="intent-list">{learning.recentQuestions.slice(0, 8).map(entry => <div className="intent-row" key={`${entry.Question}-${entry.CreatedTimestamp}`}>
                <b>{entry.Question}</b>
                <small>{entry.ConfirmedIntent ? 'you confirmed this reading — it is training data now' : 'read as it is shown here'}</small>
                <em>{entry.Rating > 0 ? 'helpful' : entry.Rating < 0 ? 'not helpful' : 'no vote'}</em>
              </div>)}</div>
            </>}

            <p className="modal-note">{learning.learningRule}</p>
          </>}
        </div>

        <div className="chat-wrap">
          <CopilotChat
            api={API}
            business={profile?.businessName ?? 'your business'}
            sample={profile?.sampleQuestion ?? 'Which batch needs attention first?'}
            expanded
            queued={queued}
            onLearning={info => setLearning(current => current ? { ...current, learnedExamples: info.learnedExamples, turns: info.turns } : current)}
          />
        </div>
      {activeTab === 'Copilot AI' && <div className="tab-panel">
        {/* The decision log sits where decisions are made: the copilot turns a
            question into a recommendation, and this is the record of what was
            then approved or declined. */}
        <h2>Decisions that protect margin</h2>
        {history.length === 0
          ? <>
              <p>No decisions logged yet. Approve or decline a yield action and it is recorded here automatically, labelled with the item and sector it belonged to.</p>
              <button className="primary-action" onClick={() => setActiveTab('Inventory')}>Review live {noun} <ArrowUpRight size={15}/></button>
            </>
          : <div className="history-list">{history.map(entry => <div className="history-row" key={entry.strategyId}>
              <div className="item-name"><div className={`item-icon ${entry.category.toLowerCase().replace(/[^a-z]/g,'')}`}>{glyph(entry.category)}</div><div><b>{entry.itemLabel}</b><small>{entry.category} · ID-{entry.itemId}</small></div></div>
              <div className="history-meta"><small>DECISION</small><span className={`decision ${entry.managerDecision.toLowerCase()}`}>{entry.managerDecision}</span></div>
              <div className="history-meta"><small>UNITS FILLED</small><b>{entry.unitsFilled}</b></div>
              <div className="history-meta"><small>MARGIN RETAINED</small><b>{money(entry.marginRetained)}</b></div>
              <div className="history-meta"><small>LOGGED</small><b>{new Date(entry.createdTimestamp).toLocaleString('en-ZA', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' })}</b></div>
            </div>)}</div>}
      </div>}

      </>}

      {(activeTab === 'Overview' || activeTab === 'Inventory') && <>
        <div className="section-heading">
          <div>
            <p className="eyebrow">{activeTab === 'Inventory' ? `${noun.toUpperCase()} CONTROL` : `LIVE ${noun.toUpperCase()}`}</p>
            <h3>{activeTab === 'Inventory' ? 'All capacity, one clear action' : 'Where attention creates value'}</h3>
          </div>
          <div className="tools">
            <button className="filter" aria-pressed={onlyAtRisk} onClick={() => setOnlyAtRisk(!onlyAtRisk)}><Filter size={15}/>{onlyAtRisk ? 'Showing at risk' : 'Filter'} <span>{atRisk.length}</span></button>
            {onlyAtRisk && <button className="view-all" onClick={() => setOnlyAtRisk(false)}>Show all <ChevronRight size={16}/></button>}
          </div>
        </div>
        {notice && <div className="notice">{notice}</div>}
        <div className="table-card">
          <div className="table-head"><span>{noun.toUpperCase()}</span><span>PRICE FLOOR</span><span>TIME LEFT</span><span>VELOCITY</span><span>STATUS</span><span/></div>
          {visible.length === 0
            ? !loaded
              ? <div className="table-empty">Reading live {noun}…</div>
              : items.length === 0
                ? <div className="table-empty">
                    <b>No {noun} in this workspace yet.</b>
                    Nothing can be ranked, costed or answered about until there are rows to read. Link a database
                    on the OpenEdge tab and its stock lands here - the same rows the copilot will answer from.
                    <div className="model-add-actions">
                      <button className="primary-action" onClick={() => setActiveTab('OpenEdge')}>Link this database <ArrowUpRight size={15}/></button>
                    </div>
                  </div>
                : <div className="table-empty">Nothing is at risk. All {items.length} lines are tracking above the floor.</div>
            : visible.map(item => <div className="table-row" key={item.itemId}>
                <div className="item-name" title={`${item.itemName} - ${item.category}, line ${item.itemId}. The subject of any question you ask about it.`}><div className={`item-icon ${item.category.toLowerCase().replace(/[^a-z]/g,'')}`}>{glyph(item.category)}</div><div><b>{item.itemName}</b><small>{item.category} · ID-{item.itemId}</small></div></div>
                <strong title={`Shelf price ${money(item.basePrice)} - what a customer pays today. Your floor on this line is ${profile?.marginFloorPct ?? 60}% of that: ${money(Math.round(item.basePrice * (profile?.marginFloorPct ?? 60) / 100))}. Nothing is recommended below it.`}>{money(item.basePrice)}</strong>
                <div className="time" title="How long is left before this line lapses, counted from its expiry - not an estimate."><Clock3 size={15}/><b>{item.hoursRemaining < 24 ? `${Math.max(1, Math.round(item.hoursRemaining))}h` : `${Math.round(item.hoursRemaining / 24)}d`}</b><small>remaining</small></div>
                <div className="velocity" title={`Share of this line already sold: ${item.unitsSold} of ${item.totalCapacity}. With the hours left, this is what sets the urgency.`}><div className="bar"><i style={{ width:`${Math.min(100, item.velocityIndex * 100)}%` }}/></div><b>{Math.round(item.velocityIndex * 100)}%</b></div>
                <div><span className={`status ${item.riskStatus}`} title={item.riskStatus === 'critical' ? 'Critical: little sold and little time left, so this line is first in line for attention.' : item.riskStatus === 'watch' ? 'Watch: time is passing faster than it is selling. Worth a look before close.' : 'Healthy: selling at a pace that should clear before it lapses.'}>{item.riskStatus === 'healthy' ? 'Healthy' : item.riskStatus === 'critical' ? 'Critical' : 'Watch'}</span></div>
                <button className="action-btn" title="Costs this line three ways - let it lapse, or add value instead of cutting the price - and shows what each one keeps against its floor." onClick={() => analyze(item)}>Generate yield action <ArrowUpRight size={15}/></button>
              </div>)}
        </div>
      </>}
      <footer><span>ValueAnchor v1.1 · {profile?.currencyCode ?? 'ZAR'} <i/> {profile?.businessName ?? 'ValueAnchor'}</span><span><span className="online"/> All systems operational</span></footer>
    </section>

    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><div className="modal" onClick={event => event.stopPropagation()}>
      <button className="close" aria-label="Close" onClick={() => setSelected(null)}><X/></button>
      {loading
        ? <div className="loading"><div className="spinner"/><p>Reading {noun} signals…</p></div>
        : analysis && <>
            <p className="eyebrow accent">YIELD ACTION / {analysis.item.itemName.toUpperCase()} · ID-{analysis.item.itemId}</p>
            <h2>{analysis.recommendation.headline}</h2>
            <p className="modal-copy">{analysis.recommendation.action}</p>
            <div className="signal-grid">
              <div><small>VELOCITY INDEX</small><b>{Math.round(analysis.metrics.velocityIndex * 100)}%</b></div>
              <div><small>URGENCY SCORE</small><b>{analysis.metrics.urgencyScore.toFixed(3)}</b></div>
              <div><small>MARGIN PRESERVED</small><b>{money(analysis.metrics.marginPreserved)}</b></div>
              <div><small>FLOOR STATUS</small><b className={analysis.metrics.floorRespected ? 'ok' : 'bad'}>{analysis.metrics.floorRespected ? 'Held' : 'Breached'}</b></div>
            </div>              <p className="modal-note">Floor kept at {money(analysis.item.basePrice)}. This business holds {profile?.marginFloorPct ?? 60}%, which is {money(analysis.metrics.marginThreshold)} on this line, and the bundle leaves {money(analysis.metrics.marginPreserved)}.{analysis.metrics.deferredCommitment > 0 && ` A further ${money(analysis.metrics.deferredCommitment)} is earned on the next visit, not charged today.`} The numbers here come from the live stock and the floor — not a saved report.</p>
            <div className="tiers">{analysis.recommendation.tiers.map(tier => <div className={`tier ${tier.status}`} key={tier.tier}>
              <span>{tier.tier}</span>
              <div><small>TIER {tier.tier} · {tier.label}</small><b>{tier.perk?.PerkName ?? 'No perk offered'}</b></div>
              {tier.status === 'withheld'
                ? <em className="tier-status">Held back: breaks the floor</em>
                : <strong>{tier.status === 'deferred' ? `${money(tier.perk?.CostToBusiness ?? 0)} next visit` : `+${money(tier.perk?.RetailValue ?? 0)} value`}</strong>}
            </div>)}</div>
            {analysis.profit && <div className="profit-block">
              <p className="eyebrow accent">WHAT EACH CHOICE KEEPS</p>
              <div className="scenario-grid">
                {analysis.profit.scenarios.map(option => <div className={`scenario ${option.key}${option.key === analysis.profit.recommended ? ' picked' : ''}`} key={option.key}>
                  <div className="scenario-head"><b>{option.label}</b>{option.key === analysis.profit.recommended && <span className="scenario-badge">recommended</span>}</div>
                  <strong>{money(option.cashPerUnit)}<small>/ unit</small></strong>
                  <span className="scenario-sub">{option.unitsMoved > 0 ? `about ${option.unitsMoved} units expected to move` : 'per unit, all else equal'}</span>
                  <p>{option.detail}</p>
                  <span className={`scenario-flag ${option.floorRespected ? 'ok' : 'bad'}`}>{option.floorRespected ? 'floor held' : 'breaks the floor'}</span>
                </div>)}
              </div>
              {analysis.profit.breakeven && <p className="breakeven"><b>Where the bundle wins:</b> {analysis.profit.breakeven.explanation}</p>}
              <p className="meta-note">{analysis.profit.notModelled}</p>
            </div>}
            <div className="modal-actions"><button className="decline" onClick={() => decide('Declined')}>Decline</button><button className="approve" onClick={() => decide('Approved')}><Sparkles size={16}/>Approve and activate</button></div>
          </>}
    </div></div>}
  </main>
}

function tabIcon(tab:Tab) {
  const kind = TAB_ICON[tab]
  if (kind === 'bars') return <BarChart3 size={18}/>
  if (kind === 'package') return <PackageCheck size={18}/>
  if (kind === 'plug') return <Plug size={18}/>
  if (kind === 'settings') return <Settings2 size={18}/>
  return <Sparkles size={18}/>
}// One line of explanation per kind of retrieved record, so the retrieval panel
// says what the copilot can look up rather than naming an internal category.
const TYPE_NOTE: Record<string, string> = {
  stock: 'every line on the counter right now, with its price, time left and rate of sale',
  perk: 'what you can give away instead of cutting a price, and what each one costs you',
  rule: 'your margin floor and the rule that nothing is sold below it',
  glossary: 'plain-language definitions for every internal term, so nothing is jargon',
  decision: 'what you approved or refused, with the margin it kept',
  sector: 'the other kinds of business the same engine can run',
  learned: 'questions you asked and confirmed the meaning of',
}



// `hint` is the hover line: where a figure is counted from, so nobody has to guess
// whether a number is a count, a forecast or a setting.
function Metric({icon,label,value,detail,tone,hint}:{icon:React.ReactNode;label:string;value:string;detail:string;tone:string;hint?:string}) {
  return <div className="metric" title={hint}><div className={`metric-icon ${tone}`}>{icon}</div><div><small>{label}</small><strong>{value}</strong><span>{detail}</span></div></div>
}
