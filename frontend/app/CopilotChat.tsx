'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, BadgeCheck, BookOpen, Check, ChevronDown, CornerDownRight, Gauge, Layers, RefreshCw, Send, ShieldAlert, ShieldCheck, Sparkles, ThumbsDown, ThumbsUp, Wrench } from 'lucide-react'

// ---------------------------------------------------------------------------
// Types: exactly what POST /api/copilot returns.
// ---------------------------------------------------------------------------
type Step = { label: string; detail: string; ask?: string }
type Figure = { label: string; value: string; note: string }
type Glossary = { term: string; meaning: string }
type Source = { id: string; kind: string; title: string; detail: string; relevance: number; itemId?: number | null }
type Attempt = { model: string; status: string; detail?: string }
type Severity = 'act-now' | 'opportunity' | 'watch' | 'caution' | 'blocked' | 'info'

export type CopilotAnswer = {
  answer: string
  engineAnswer: string
  source: string
  itemId: number | null
  headline: string
  meaning: string
  plain: string
  severity: Severity
  nextSteps: Step[]
  keyNumbers: Figure[]
  followUps: string[]
  glossary: Glossary[]
  sources: Source[]
  retrieval: { documents: number; byKind: Record<string, number>; glossaryTerms: number; terms: number; builtFrom: string }
  assistant: { used: boolean; model: string | null; onTopic: boolean; declined: boolean; declineReason: string; guardrail: string; attempted: Attempt[]; chain: string[] }
  guardrail: string
  inScope: boolean
  turnId: number
  nlu: {
    intent: string
    intentLabel: string
    intentDescription: string
    confidence: number
    alternatives: { intent: string; label: string; confidence: number }[]
    entities: { type: string; value: string | number; matchedOn?: string }[]
    corrections: { from: string; to: string }[]
    needsClarification: boolean
    resolvedFromContext: boolean
    reply: { clarifyOptions: { intent: string; label: string }[]; reasoning: string[]; how: string }
    intentConfirmed?: boolean
  }
  learning?: { learnedExamples: number; turns: number; helpful: number }
}

type Suggestions = {
  groups: { title: string; detail: string; questions: string[] }[]
  worstItem?: string
  focus?: { item: string; hoursRemaining: number; riskStatus: string }
}

type Turn = { TurnID: number; Question: string; Answer: string; Intent: string; CreatedTimestamp: string; Payload?: CopilotAnswer }

type Message =
  | { key: string; role: 'user'; text: string }
  | { key: string; role: 'copilot'; data: CopilotAnswer }
  | { key: string; role: 'error'; text: string }

// The four things that actually happen, in order, for every question. Shown
// while waiting so the pause reads as work rather than as nothing happening.
const STAGES = [
  'Reading your question',
  'Finding the records that matter',
  'Working out the numbers',
  'Putting it in plain words',
]

const SEVERITY: Record<Severity, { label: string; tone: string }> = {
  'act-now': { label: 'Act now', tone: 'red' },
  opportunity: { label: 'Opportunity', tone: 'green' },
  watch: { label: 'Worth watching', tone: 'amber' },
  caution: { label: 'Use with care', tone: 'amber' },
  blocked: { label: 'Not doing that', tone: 'slate' },
  info: { label: 'For context', tone: 'blue' },
}

const KIND_LABEL: Record<string, string> = {
  stock: 'Your stock',
  perk: 'A perk you can offer',
  rule: 'Your own rule',
  glossary: 'A word explained',
  sector: 'Another business type',
  decision: 'Something you decided',
  learned: 'Something you taught it',
}

const SESSION_KEY = 'valueanchor-session'

function sessionId() {
  if (typeof window === 'undefined') return 'web-session'
  const existing = window.localStorage.getItem(SESSION_KEY)
  if (existing) return existing
  const fresh = `web-${Math.random().toString(36).slice(2, 10)}`
  window.localStorage.setItem(SESSION_KEY, fresh)
  return fresh
}

export default function CopilotChat({
  api,
  business,
  sample,
  expanded = false,
  queued,
  onLearning,
}: {
  api: string
  business: string
  sample: string
  expanded?: boolean
  queued?: { text: string; nonce: number } | null
  onLearning?: (info: { learnedExamples: number; turns: number }) => void
}) {
  const [session] = useState(sessionId)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState(0)
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null)
  const [openSources, setOpenSources] = useState<Record<string, boolean>>({})
  const [showRationale, setShowRationale] = useState<Record<string, boolean>>({})
  const [voted, setVoted] = useState<Record<number, number>>({})
  const [restored, setRestored] = useState(false)
  const streamRef = useRef<HTMLDivElement | null>(null)
  const consumed = useRef(0)

  // -- data ------------------------------------------------------------
  const loadSuggestions = useCallback(() => {
    fetch(`${api}/api/copilot/suggestions`)
      .then(response => (response.ok ? response.json() : null))
      .then(setSuggestions)
      .catch(() => setSuggestions(null))
  }, [api])

  useEffect(() => {
    loadSuggestions()
  }, [loadSuggestions])

  // Reopening the copilot restores the conversation, including the figures and
  // the citations, because the server stores the whole answer and not just the
  // prose.
  useEffect(() => {
    let alive = true
    fetch(`${api}/api/copilot/history?sessionId=${encodeURIComponent(session)}&limit=40`)
      .then(response => (response.ok ? response.json() : { turns: [] }))
      .then((body: { turns: Turn[] }) => {
        if (!alive) return
        const restoredMessages: Message[] = []
        for (const turn of body.turns || []) {
          restoredMessages.push({ key: `q-${turn.TurnID}`, role: 'user', text: turn.Question })
          if (turn.Payload && turn.Payload.headline) {
            restoredMessages.push({ key: `a-${turn.TurnID}`, role: 'copilot', data: turn.Payload })
          }
        }
        if (restoredMessages.length) setMessages(restoredMessages)
      })
      .catch(() => undefined)
      .finally(() => alive && setRestored(true))
    return () => {
      alive = false
    }
  }, [api, session])

  useEffect(() => {
    if (!busy) return
    setStage(0)
    const timer = window.setInterval(() => setStage(current => Math.min(current + 1, STAGES.length - 1)), 900)
    return () => window.clearInterval(timer)
  }, [busy])

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  // -- asking ----------------------------------------------------------
  const ask = useCallback(
    async (text: string, intent?: string) => {
      const question = text.trim()
      if (!question || busy) return
      setDraft('')
      setMessages(current => [...current, { key: `q-${Date.now()}`, role: 'user', text: question }])
      setBusy(true)
      try {
        const response = await fetch(`${api}/api/copilot`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question, sessionId: session, intent }),
        })
        if (!response.ok) throw new Error('copilot failed')
        const data: CopilotAnswer = await response.json()
        setMessages(current => [...current, { key: `a-${data.turnId}-${Date.now()}`, role: 'copilot', data }])
        if (data.learning && onLearning) {
          onLearning({ learnedExamples: data.learning.learnedExamples, turns: data.learning.turns })
        }
        loadSuggestions()
      } catch {
        setMessages(current => [
          ...current,
          {
            key: `e-${Date.now()}`,
            role: 'error',
            text: 'The copilot could not be reached. Start the backend on port 8000 and ask again.',
          },
        ])
      } finally {
        setBusy(false)
      }
    },
    [api, busy, loadSuggestions, onLearning, session],
  )

  // A question sent from outside the chat, so a chip elsewhere on the page can
  // reuse the same conversation rather than opening a second one.
  useEffect(() => {
    if (!queued || queued.nonce === consumed.current) return
    consumed.current = queued.nonce
    ask(queued.text)
  }, [queued, ask])

  const vote = async (turnId: number, rating: number, intent?: string) => {
    setVoted(current => ({ ...current, [turnId]: rating }))
    try {
      const response = await fetch(`${api}/api/copilot/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ turnId, rating, intent }),
      })
      const body = await response.json()
      if (body.learning && onLearning) onLearning(body.learning)
    } catch {
      /* a lost vote is not worth an error message */
    }
  }

  const confirmIntent = async (turnId: number, intent: string) => {
    try {
      const response = await fetch(`${api}/api/copilot/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ turnId, intent, rating: 1 }),
      })
      const body = await response.json()
      setMessages(current =>
        current.map(message =>
          message.role === 'copilot' && message.data.turnId === turnId
            ? { ...message, data: { ...message.data, nlu: { ...message.data.nlu, intentConfirmed: true } } }
            : message,
        ),
      )
      if (body.learning && onLearning) onLearning(body.learning)
    } catch {
      /* ignore */
    }
  }

  const noun = expanded ? 'the counter' : business
  const empty = messages.length === 0 && !busy

  return (
    <section className={`chat${expanded ? ' chat-expanded' : ''}`}>
      <header className="chat-head">
        <div className="chat-mark"><Sparkles size={15} /></div>
        <div>
          <p className="eyebrow accent">VALUEANCHOR COPILOT</p>
          <h3>{empty ? 'Ask about your live stock.' : 'Your conversation'}</h3>
        </div>
        <span className="chat-state"><i /> Grounded in {noun}</span>
      </header>

      <p className="chat-lede">
        Answers are worked out from your live stock, with every figure labelled and anything below your
        floor refused rather than guessed.
      </p>

      {!empty && (
        <div className="chat-stream" ref={streamRef}>
          {messages.map(message => {
            if (message.role === 'user') {
              return (
                <div className="bubble user" key={message.key}>
                  <p>{message.text}</p>
                </div>
              )
            }
            if (message.role === 'error') {
              return <div className="bubble error" key={message.key}>{message.text}</div>
            }
            return (
              <AnswerCard
                key={message.key}
                data={message.data}
                openSources={openSources}
                setOpenSources={setOpenSources}
                showRationale={showRationale}
                setShowRationale={setShowRationale}
                voted={voted}
                vote={vote}
                confirmIntent={confirmIntent}
                ask={ask}
              />
            )
          })}

          {busy && (
            <div className="bubble copilot working">
              <ol className="stages">
                {STAGES.map((label, index) => (
                  <li key={label} className={index < stage ? 'done' : index === stage ? 'active' : ''}>
                    <span className="stage-dot">{index < stage ? <Check size={10} /> : index + 1}</span>
                    {label}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {empty && (
        <div className="starters">
          {suggestions && suggestions.groups.length > 0 && (
            <article className="starter-group">
              <b>Start with a question</b>
              <small>Each answer is worked out from your live numbers.</small>
              <div className="phrase-chips">
                {suggestions.groups.flatMap(group => group.questions).slice(0, 4).map(question => (
                  <button key={question} onClick={() => ask(question)}>{question}</button>
                ))}
              </div>
            </article>
          )}
          {suggestions && suggestions.groups.length === 0 && (
            <article className="starter-group">
              <b>There is no stock to ask about yet</b>
              <small>Link a database on the OpenEdge tab and the rows become askable here.</small>
            </article>
          )}
          {!suggestions && (
            <article className="starter-group">
              <b>Try one of these</b>
              <small>The answers come from your live numbers, not from a script.</small>
              <div className="phrase-chips">
                <button onClick={() => ask(sample)}>{sample}</button>
                <button onClick={() => ask('what is my margin floor?')}>What is my margin floor?</button>
              </div>
            </article>
          )}
        </div>
      )}

      <form
        className="chat-input"
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          ask(draft)
        }}
      >
        <input
          aria-label="Ask ValueAnchor Copilot"
          value={draft}
          onChange={event => setDraft(event.target.value)}
          placeholder={`Ask in your own words, for example: ${sample}`}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          {busy ? <><RefreshCw className="spin" size={14} /> Working…</> : <><Send size={14} /> Ask</>}
        </button>
      </form>

      {restored && messages.length > 0 && (
        <small className="chat-foot">
          This conversation is remembered on this device, so reopening the page keeps it here.
        </small>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// One answer, laid out so the owner can act on it without decoding anything.
// ---------------------------------------------------------------------------
function AnswerCard({
  data,
  openSources,
  setOpenSources,
  showRationale,
  setShowRationale,
  voted,
  vote,
  confirmIntent,
  ask,
}: {
  data: CopilotAnswer
  openSources: Record<string, boolean>
  setOpenSources: (value: Record<string, boolean>) => void
  showRationale: Record<string, boolean>
  setShowRationale: (value: Record<string, boolean>) => void
  voted: Record<number, number>
  vote: (turnId: number, rating: number, intent?: string) => void
  confirmIntent: (turnId: number, intent: string) => void
  ask: (text: string, intent?: string) => void
}) {
  const severity = SEVERITY[data.severity] || SEVERITY.info
  const sources = openSources[data.turnId]
  const rationale = showRationale[data.turnId]
  const refusals = useMemo(() => data.assistant.attempted.filter(attempt => attempt.status !== 'answered'), [data.assistant.attempted])
  // Suggested follow-ups that are already offered as a button above would just
  // be the same question twice, so they are dropped rather than repeated.
  const followUps = useMemo(() => {
    const offered = new Set(data.nextSteps.map(step => (step.ask || '').toLowerCase().trim()).filter(Boolean))
    return data.followUps.filter(question => !offered.has(question.toLowerCase().trim()))
  }, [data.followUps, data.nextSteps])
  const answeredBy = data.assistant.used
    ? `Reworded for you by a language model, then checked figure by figure`
    : `Answered by the engine on this machine, with no language model in the loop`

  return (
    <article className="bubble copilot answer-card">
      <div className="answer-top">
        <span className={`severity ${severity.tone}`}>{severity.label}</span>
        <span className="answer-intent">{data.nlu.intentLabel}</span>
        {data.assistant.used && <span className="answer-tag"><Sparkles size={10} /> explained for you</span>}
        {!data.inScope && <span className="answer-tag refused"><ShieldAlert size={10} /> stayed local</span>}
      </div>

      <h4>{data.headline}</h4>
      <p className="answer-prose">{data.answer}</p>

      {data.meaning && (
        <div className="answer-meaning">
          <b>What this means</b>
          <p>{data.meaning}</p>
        </div>
      )}

      {data.keyNumbers.length > 0 && (
        <>
          <div className="figures">
            {data.keyNumbers.slice(0, 6).map(figure => (
              <div className="figure" key={figure.label}>
                <small>{figure.label}</small>
                <strong>{figure.value}</strong>
                <span>{figure.note}</span>
              </div>
            ))}
          </div>
          {data.keyNumbers.length > 6 && (
            <details className="more-figures">
              <summary>{data.keyNumbers.length - 6} more figures from this answer</summary>
              <div className="figures">
                {data.keyNumbers.slice(6).map(figure => (
                  <div className="figure" key={figure.label}>
                    <small>{figure.label}</small>
                    <strong>{figure.value}</strong>
                    <span>{figure.note}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}

      {data.glossary.length > 0 && (
        <div className="explainers">
          <p className="label"><BookOpen size={11} /> Words in that answer, explained</p>
          {data.glossary.map(entry => (
            <p className="explainer" key={entry.term}>
              <b>{entry.term}</b> {entry.meaning}
            </p>
          ))}
        </div>
      )}

      {data.nextSteps.length > 0 && (
        <div className="next-steps">
          <p className="label"><Wrench size={11} /> What you can do about it</p>
          {data.nextSteps.map(step => (
            <div className="step" key={step.label}>
              <div>
                <b>{step.label}</b>
                <small>{step.detail}</small>
              </div>
              {step.ask && (
                <button onClick={() => ask(step.ask as string)}>
                  Ask <ArrowRight size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {data.nlu.needsClarification && data.nlu.reply.clarifyOptions.length > 0 && (
        <div className="clarify-row">
          <small>I can read that more than one way — which did you mean?</small>
          <div className="phrase-chips" title="Picking one teaches the model your phrasing for good. Either way the answer is priced against your live numbers.">
            {data.nlu.reply.clarifyOptions.map((option, index) => (
              <button
                key={`${option.intent}-${index}`}
                disabled={data.nlu.intentConfirmed}
                onClick={() => (data.nlu.intentConfirmed ? undefined : confirmIntent(data.turnId, option.intent))}
              >
                {index === 0 ? `Yes — ${option.label.toLowerCase()}` : option.label}
              </button>
            ))}
          </div>
          <small className="clarify-note">
            {data.nlu.intentConfirmed
              ? 'Stored. It will read that phrasing this way from now on without asking.'
              : 'Picking one teaches this model your phrasing for good, and answers with the live numbers either way.'}
          </small>
        </div>
      )}

      {followUps.length > 0 && (
        <div className="follow-ups">
          <p className="label"><CornerDownRight size={11} /> Usually asked next</p>
          <div className="phrase-chips">
            {followUps.map(question => (
              <button key={question} onClick={() => ask(question)}>{question}</button>
            ))}
          </div>
        </div>
      )}

      <div className="answer-actions">
        <button
          className="work-toggle"
          title="Opens what this answer read and the arithmetic it did: the intent, the records, and each figure with its source."
          onClick={() => setShowRationale({ ...showRationale, [data.turnId]: !rationale })}
        >
          <Gauge size={12} /> How it worked this out <ChevronDown size={12} className={rationale ? 'flip' : ''} />
        </button>
        <button
          className="work-toggle"
          title="The records this question retrieved out of your live data, each with its kind and how relevant it was."
          onClick={() => setOpenSources({ ...openSources, [data.turnId]: !sources })}
        >
          <Layers size={12} /> Where this came from ({data.sources.length}) <ChevronDown size={12} className={sources ? 'flip' : ''} />
        </button>
        <div className="vote">
          <button
            aria-label="This was helpful"
            title="Marks this answer useful. A vote never changes the model on its own - only a reading you confirm does."
            className={voted[data.turnId] === 1 ? 'on' : ''}
            onClick={() => vote(data.turnId, 1, data.nlu.intent)}
          >
            <ThumbsUp size={12} />
          </button>
          <button
            aria-label="This was not helpful"
            title="Marks this answer unhelpful. It is recorded but never taught, so a wrong answer cannot be reinforced."
            className={voted[data.turnId] === -1 ? 'bad' : ''}
            onClick={() => vote(data.turnId, -1)}
          >
            <ThumbsDown size={12} />
          </button>
        </div>
      </div>

      {rationale && (
        <div className="panel">
          <p className="label">How it worked this out</p>
          <ol>{data.nlu.reply.reasoning.map((line, index) => <li key={index}>{line}</li>)}</ol>
          <div className="meta-row">
            <span>{Math.round(data.nlu.confidence * 100)}% sure what you asked</span>
            {data.nlu.entities.length > 0 && (
              <span>
                read out of your sentence:{' '}
                {data.nlu.entities
                  .map(entity => (entity.type === 'percent' ? `${entity.value}%` : String(entity.value)))
                  .join(', ')}
              </span>
            )}
            {data.nlu.resolvedFromContext && <span>this followed on from your last question</span>}
            {data.nlu.corrections.length > 0 && (
              <span>typo repaired: {data.nlu.corrections.map(fix => `${fix.from} → ${fix.to}`).join(', ')}</span>
            )}
          </div>
          {data.nlu.alternatives.length > 0 && (
            <div className="phrase-chips">
              <span>It also considered:</span>
              {data.nlu.alternatives.map(option => (
                <span key={option.intent}>{option.label} · {Math.round(option.confidence * 100)}%</span>
              ))}
            </div>
          )}
          <p className="label">What answered</p>
          <ul className="plain-list">
            <li>{answeredBy}</li>
            <li>{data.guardrail}</li>
            {refusals.map(attempt => (
              <li key={attempt.model}>
                {attempt.model} did not answer ({attempt.status}), so the next one was tried
              </li>
            ))}
            {!data.assistant.used && data.assistant.chain.length > 0 && (
              <li>Models available if needed: {data.assistant.chain.length} on a free tier, tried in order</li>
            )}
          </ul>
        </div>
      )}

      {sources && (
        <div className="panel">
          <p className="label">
            <BadgeCheck size={11} /> The records this answer was written from
          </p>
          <ul className="source-list">
            {data.sources.map(source => (
              <li key={source.id}>
                <span className="source-kind">{KIND_LABEL[source.kind] || source.kind}</span>
                <b>{source.title}</b>
                <small>{source.detail}</small>
              </li>
            ))}
            {data.sources.length === 0 && <li className="muted">No records were needed for this answer.</li>}
          </ul>
          <p className="meta-note">
            {data.retrieval.documents} records are indexed for this business
            {data.retrieval.byKind?.stock ? `, including all ${data.retrieval.byKind.stock} live lines` : ''}.
            {data.inScope
              ? ' The language model was handed this list and nothing else, and any figure of its own that was not in these records would have been thrown away.'
              : ' Because this was not about your business, no records were looked up and no model was asked.'}
          </p>
          {data.assistant.used && (
            <p className="meta-note">
              <ShieldCheck size={11} /> Every figure in this answer was checked against the records above.
            </p>
          )}
        </div>
      )}
    </article>
  )
}
