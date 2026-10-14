"""The optional language assistant: it explains, it never decides.

The local engine already knows what was asked and has already computed the
answer from live rows. This module exists because knowing the answer and being
able to *explain* it are different skills: "urgency 0.42" is the right internal
decision and the wrong sentence for the person running a coffee shop.

So the assistant's contract is deliberately narrow:

* It is handed the engine's answer plus the documents retrieval selected, and is
  asked to say the same thing in plain words and explain the figures.
* It is **not** asked what to do, what the numbers are, or whether the floor is
  respected. Those are already settled before it is called.
* It is only called when the local engine is confident the question is about this
  business at all. An off-topic question never reaches the network.

Guardrails, in order, and all of them fail closed:

1. **Local scope gate.** No business intent and no business entity means no call.
   The LLM is never asked to police itself on the one question where a mistake
   would be most embarrassing.
2. **Retrieval grounding.** The prompt contains only retrieved records. The model
   is told that anything outside them does not exist.
3. **Self-declared refusal.** The model returns ``onTopic: false`` when the
   question is out of scope. That refusal is honoured.
4. **Number audit.** Every figure in the model's answer must already appear in
   the grounded context. A model that invents a number has its answer discarded
   and the local answer is used instead.

Guardrail 4 is the one that matters most, and it is the one most systems skip.
A language model that rephrases a correct answer is useful; a language model that
quietly changes R23.40 into R27 is a liability, and no amount of prompt
politeness fixes that. Checking the arithmetic after the fact does.

The free tier allows 20 requests a minute and 50 a day, so the models in the
chain are the ones that were verified to answer this task with valid JSON.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

# The registry owns the default chain and the provider list, so there is one
# place to look for "which models, talking to what". Imported rather than
# re-declared: two lists of model names drift apart within a week.
from .models import DEFAULT_MODEL_CHAIN, DEFAULT_PROVIDER, OPENROUTER_BASE, provider_for

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
MAX_CONTEXT_DOCS = 8


@dataclass
class AssistantResult:
    """What the assistant did, in enough detail to be audited in the UI."""

    used: bool = False
    model: str | None = None
    answer: str = ""
    explanation: str = ""
    on_topic: bool = True
    declined: bool = False
    decline_reason: str = ""
    guardrail: str = ""
    attempted: list[dict[str, Any]] = field(default_factory=list)
    tokens: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "model": self.model,
            "answer": self.answer,
            "explanation": self.explanation,
            "onTopic": self.on_topic,
            "declined": self.declined,
            "declineReason": self.decline_reason,
            "guardrail": self.guardrail,
            "attempted": self.attempted,
            "chain": [spec["model"] for spec in getattr(self, "_chain", [])] or list(DEFAULT_MODEL_CHAIN),
        }


class CopilotAssistant:
    """Trains nothing, decides nothing, explains everything."""

    def __init__(
        self,
        api_key: str | None,
        model: str | None = None,
        fallbacks: list[str] | None = None,
        timeout: float = 30.0,
        *,
        chain_specs: list[dict[str, Any]] | None = None,
        on_attempt: Callable[[dict[str, Any], dict[str, Any], dict[str, str]], None] | None = None,
    ) -> None:
        """Build the assistant over an ordered list of models.

        Two ways in, because both callers are legitimate:

        * ``chain_specs`` from the registry, where each entry carries its own
          base URL and the name of the environment variable holding its key.
        * a plain ``api_key`` plus model names, which is what the tests and any
          single-provider setup use.

        ``on_attempt`` is called after every request with the spec, the outcome
        and the raw response headers. It is how rate-limit state gets out of this
        module without this module knowing there is a database at all.
        """
        self.api_key = (api_key or "").strip() or None
        self.timeout = timeout
        self.on_attempt = on_attempt
        self.last_error: str | None = None

        # ``is not None`` rather than truthiness: an empty list is a deliberate
        # instruction ("every model is switched off"), not a request for defaults.
        if chain_specs is not None:
            self._chain = [dict(spec) for spec in chain_specs]
        else:
            names = [name for name in [model, *(fallbacks or [])] if name]
            ordered = list(dict.fromkeys(names)) or list(DEFAULT_MODEL_CHAIN)
            self._chain = [
                {
                    "model": name,
                    "provider": DEFAULT_PROVIDER,
                    "baseUrl": OPENROUTER_BASE,
                    "apiKeyEnv": "OPENROUTER_API_KEY",
                }
                for name in ordered
            ]

        # Kept for readability at call sites and in logs: the model ids in order.
        self.chain = [spec["model"] for spec in self._chain]

    def _key_for(self, spec: dict[str, Any]) -> str | None:
        """The key for one model: its own environment variable, or the default."""
        env_name = spec.get("apiKeyEnv")
        if env_name:
            value = (os.environ.get(env_name) or "").strip()
            if value:
                return value
        return self.api_key

    @property
    def configured(self) -> bool:
        """True when at least one model in the chain has a usable key."""
        if self.api_key:
            return True
        return any(self._key_for(spec) for spec in self._chain)

    # -- scope ------------------------------------------------------------
    @staticmethod
    def local_scope_gate(
        intent: str,
        entities: list[dict[str, Any]],
        known_intents: set[str],
    ) -> tuple[bool, str]:
        """Decide - without any network call - whether the question is in scope.

        This runs before the assistant is consulted, so an off-topic question is
        refused by deterministic code rather than by asking a language model to
        behave. The gate is deliberately conservative: it looks for a business
        entity (a stock line, a percentage, money, a duration, a quantity or a
        time window) or a business intent that the local model recognised with
        confidence.
        """
        if intent == "out_of_scope":
            return False, "the local model read this as off topic"
        business_kinds = {"item", "category", "percent", "money", "duration", "quantity", "time_window"}
        if any(entity.get("type") in business_kinds for entity in entities):
            return True, ""
        if intent in known_intents and intent not in {"out_of_scope"}:
            return True, ""
        return False, "no business vocabulary was recognised, so nothing may be answered"

    # -- numbers ----------------------------------------------------------
    @staticmethod
    def _numbers(text: str) -> list[float]:
        out: list[float] = []
        for match in NUMBER_RE.finditer(text or ""):
            try:
                out.append(float(match.group(0).replace(",", ".")))
            except ValueError:
                continue
        return out

    @classmethod
    def _invented_numbers(cls, candidate: str, allowed_text: str) -> list[str]:
        """Figures in ``candidate`` that cannot be traced to the grounded context.

        This is the guardrail that keeps a rephrasing from becoming a rewrite. A
        number is accepted when it already appears in the context, or when it is
        the same value written differently (R23.40 versus 23.40 versus 23), which
        is what a model does when it rounds for readability.
        """
        allowed = cls._numbers(allowed_text)
        invented: list[str] = []
        for value in cls._numbers(candidate):
            if any(abs(value - okay) <= max(0.05, abs(okay) * 0.02) for okay in allowed):
                continue
            invented.append(f"{value:g}")
        return invented

    # -- prompt -----------------------------------------------------------
    def _system_prompt(
        self,
        profile: dict[str, Any],
        context: list[dict[str, Any]],
        glossary: list[dict[str, str]],
    ) -> str:
        noun = profile.get("inventoryNoun") or "stock"
        # The short `detail` is enough to ground the assistant, and keeping the
        # prompt lean matters: the free models in the chain are reasoning models
        # that spend their token budget thinking before they write anything.
        lines = []
        for doc in context[:MAX_CONTEXT_DOCS]:
            lines.append(f"[{doc.get('id')}] {doc.get('title')}: {doc.get('detail') or doc.get('text', '')[:200]}")
        terms = "\n".join(f"- {item['term']}: {item['meaning']}" for item in glossary[:6])

        return (
            f"You are the explainer inside ValueAnchor, the yield copilot for {profile.get('businessName')}, "
            f"a {str(profile.get('name', '')).lower()} that sells {noun}, with a "
            f"{profile.get('marginFloorPct')}% margin floor and {profile.get('currencyCode')} "
            f"({profile.get('currencySymbol')}) as its currency.\n"
            "\n"
            "WHO YOU ARE TALKING TO: the owner of this business. They are good at running the shop and "
            "have no interest in technical language. They do not know what a velocity index, an urgency "
            "score or a deferred tier is, and they should never have to learn.\n"
            "\n"
            "WHAT YOU DO: the engine has already decided and already worked out the answer. Your only job "
            "is to say the same thing in words a shop owner would use, and explain what the figures mean "
            "and what to do next.\n"
            "\n"
            "RULES, all of them absolute:\n"
            "1. Never change a decision. If the engine said no, refused, or withheld something, you say no, "
            "refused or withheld.\n"
            "2. Never change, add, round differently or estimate a number. Use only figures in the records "
            "below. If a figure is not below, it does not exist.\n"
            "3. Never use a piece of jargon without explaining it in the same sentence.\n"
            "4. Never mention that you are an AI, a model, a prompt, retrieval, confidence scores or the "
            "backend, unless the owner explicitly asks how the product works.\n"
            "5. Stay strictly on this business: its live stock, its perks, its pricing rules, its logged "
            "decisions and what the engine can do. Anything else - news, sport, weather, general trivia, "
            "coding, medical, legal or financial advice, or any other business - is out of scope. Set "
            "onTopic to false and leave answer and explanation empty.\n"
            "6. Keep the answer under 70 words and the explanation under 45. Short beats thorough here.\n"
            "\n"
            f"RECORDS RETRIEVED FOR THIS QUESTION (the only facts that exist):\n"
            + ("\n".join(lines) if lines else "(none)")
            + "\n"
            + (f"\nPLAIN MEANINGS OF THE TERMS YOU MAY NEED TO USE:\n{terms}\n" if terms else "")
            + "\nReply with JSON only, exactly this shape: "
            '{"onTopic": true, "answer": "...", "explanation": "..."}'
        )

    @staticmethod
    def _user_prompt(
        question: str,
        intent_label: str,
        engine_answer: str,
        next_steps: list[str],
        key_numbers: list[dict[str, Any]],
    ) -> str:
        numbers = "; ".join(
            f"{entry.get('label')} {entry.get('value')}" for entry in key_numbers[:8]
        ) or "(none)"
        steps = "; ".join(next_steps[:4]) or "(none)"
        return (
            f"The owner asked: {question}\n"
            f"The engine read that as: {intent_label}\n"
            f"The engine's answer: {engine_answer}\n"
            f"Figures the engine worked out: {numbers}\n"
            f"Actions the engine is proposing: {steps}\n"
            "\n"
            "Now write two things:\n"
            "1. answer: the engine's answer in the owner's own words. Same meaning, same figures, nothing added.\n"
            "2. explanation: what those figures mean in practice. If the answer is already plain, leave empty.\n"
            'Reply with JSON only: {"onTopic": true, "answer": "...", "explanation": "..."}'
        )

    # -- transport --------------------------------------------------------
    @staticmethod
    def _unwrap(content: str | None) -> dict[str, Any] | None:
        """Decode the reply, tolerating the shapes providers actually return.

        One of the probed free models returns its JSON object double-encoded as a
        string, and another prefixes it with whitespace. Both are cheap to handle
        and neither is worth failing an answer over.
        """
        if not content:
            return None
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
        for attempt in (text,):
            try:
                data = json.loads(attempt)
            except ValueError:
                match = re.search(r"\{.*\}", attempt, re.DOTALL)
                if not match:
                    return None
                try:
                    data = json.loads(match.group(0))
                except ValueError:
                    return None
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    return None
            if isinstance(data, dict):
                return data
        return None

    @staticmethod
    def limit_metadata(body_text: str) -> tuple[str, str]:
        """Read ``limit_source`` and the provider's own remedy out of a 429 body.

        OpenRouter explains itself: ``limit_source: openrouter_free_tier_daily``
        plus a ``remedy_hint``. That is more useful to show than anything we could
        invent, so it is read and passed through rather than replaced with a
        generic "rate limited" message nobody can act on.
        """
        try:
            payload = json.loads(body_text or "{}")
        except ValueError:
            return "", ""
        metadata = (payload.get("error") or {}).get("metadata") or {}
        return str(metadata.get("limit_source") or ""), str(metadata.get("remedy_hint") or "")

    def _ask(
        self,
        spec: dict[str, Any],
        system: str,
        user: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, str]]:
        """One request to one model, returning the reply, the outcome and the headers.

        Returns three things because the headers are not diagnostics - they are
        the rate-limit state, and the caller persists them so the next visitor can
        be told exactly when the model comes back.
        """
        model = spec.get("model") or ""
        meta: dict[str, Any] = {"model": model, "label": spec.get("label") or model, "status": "unknown"}
        key = self._key_for(spec)
        if not key:
            meta["status"] = "no key"
            meta["detail"] = f"{spec.get('apiKeyEnv') or 'the key variable'} is empty"
            return None, meta, {}

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            # Generous on purpose. Every model in the verified free chain is a
            # reasoning model: one of them spent 609 of 679 completion tokens
            # thinking, and at a 700-token cap it returned no content at all. The
            # answer itself is under 70 words, so this is headroom for thinking,
            # not for a longer reply.
            "max_tokens": 2400,
            "response_format": {"type": "json_object"},
        }
        base_url = (spec.get("baseUrl") or OPENROUTER_BASE).rstrip("/")
        try:
            with httpx.Client(base_url=base_url, timeout=self.timeout) as client:
                response = client.post(
                    "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://valueanchor.app",
                        "X-Title": "ValueAnchor Copilot",
                    },
                    json=payload,
                )
        except Exception as error:  # network failure, DNS, timeout
            meta["status"] = "unreachable"
            meta["detail"] = str(error)[:180]
            self.last_error = meta["detail"]
            return None, meta, {}

        headers = {name: value for name, value in response.headers.items()}
        if response.status_code == 429:
            meta["status"] = "rate limited"
            source, remedy = self.limit_metadata(response.text)
            meta["limitSource"] = source
            meta["detail"] = remedy or response.text[:180]
            return None, meta, headers
        if response.status_code >= 400:
            meta["status"] = f"http {response.status_code}"
            meta["detail"] = response.text[:180]
            return None, meta, headers

        try:
            body = response.json()
        except ValueError:
            meta["status"] = "unreadable response"
            return None, meta, headers

        choices = body.get("choices") or []
        if not choices:
            meta["status"] = "no choices returned"
            return None, meta, headers
        message = choices[0].get("message") or {}
        data = self._unwrap(message.get("content"))
        if data is None:
            meta["status"] = "no usable content"
            meta["finishReason"] = choices[0].get("finish_reason")
            return None, meta, headers
        meta["status"] = "answered"
        usage = body.get("usage") or {}
        if usage:
            meta["tokens"] = usage
        return data, meta, headers

    # -- public -----------------------------------------------------------
    def rewrite(
        self,
        *,
        question: str,
        intent: str,
        intent_label: str,
        engine_answer: str,
        profile: dict[str, Any],
        context: list[dict[str, Any]],
        glossary: list[dict[str, str]],
        next_steps: list[str],
        key_numbers: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        known_intents: set[str],
    ) -> AssistantResult:
        """Rephrase the engine's answer, or decline with a reason.

        Every early return here is a guardrail doing its job, and each one is
        reported so the UI can show the owner why the local answer stood.
        """
        result = AssistantResult()
        if not self.configured:
            result.guardrail = "no key configured, so the copilot answered from the local model only"
            return result

        allowed, reason = self.local_scope_gate(intent, entities, known_intents)
        if not allowed:
            result.on_topic = False
            result.declined = True
            result.decline_reason = reason
            result.guardrail = f"stayed local: {reason}"
            return result

        system = self._system_prompt(profile, context, glossary)
        user = self._user_prompt(question, intent_label, engine_answer, next_steps, key_numbers)
        allowed_text = " ".join(
            [engine_answer, question, *[str(entry.get("value")) for entry in key_numbers]]
            + [f"{doc.get('title')} {doc.get('detail')} {doc.get('text')}" for doc in context]
        )

        for spec in self._chain:
            # A model already known to be out of quota is skipped instead of
            # called, so the owner is not made to wait for four refusals that
            # were already known, and the reason stays the real one.
            if spec.get("coolingDown"):
                result.attempted.append(
                    {
                        "model": spec.get("model"),
                        "label": spec.get("label") or spec.get("model"),
                        "status": "cooling down",
                        "detail": f"out of quota, back in {spec.get('humanRemaining') or 'a while'}",
                        "coolingDown": True,
                        "humanRemaining": spec.get("humanRemaining"),
                        "secondsRemaining": spec.get("secondsRemaining"),
                    }
                )
                continue
            data, meta, headers = self._ask(spec, system, user)
            if self.on_attempt is not None:
                try:
                    self.on_attempt(spec, meta, headers)
                except Exception:  # bookkeeping must never break an answer
                    pass
            result.attempted.append(meta)
            if data is None:
                continue

            if data.get("onTopic") is False:
                result.on_topic = False
                result.declined = True
                result.decline_reason = "the assistant judged this out of scope"
                result.guardrail = "the assistant refused this as off topic, and the refusal was honoured"
                return result

            answer = str(data.get("answer") or "").strip()
            explanation = str(data.get("explanation") or "").strip()
            if not answer:
                continue

            invented = self._invented_numbers(f"{answer} {explanation}", allowed_text)
            if invented:
                # The model changed a figure. Its answer is thrown away rather
                # than patched, because a silently altered number is worse than
                # a plainer sentence.
                result.guardrail = (
                    "the assistant produced figures that are not in your live data "
                    f"({', '.join(invented[:3])}), so its wording was discarded and the engine's own answer kept"
                )
                result.attempted[-1]["status"] = "rejected by number audit"
                result.attempted[-1]["invented"] = invented[:5]
                continue

            result.used = True
            result.model = spec.get("model")
            result.answer = answer
            result.explanation = explanation
            result.guardrail = "every figure in this answer was traced back to your live data"
            return result

        if not result.guardrail:
            result.guardrail = self._failure_summary(result.attempted)
        return result

    @staticmethod
    def _failure_summary(attempted: list[dict[str, Any]]) -> str:
        """Say *why* nothing answered, and when it comes back, in one sentence.

        "The assistant was unavailable" is true and useless. If every model was
        rate limited, the owner should be told that the free quota is spent and
        when it resets - that is a fact they can act on, and it is the difference
        between a tool that looks broken and a tool that is honest about a limit.
        """
        if not attempted:
            return "no language model is configured, so the local model answered"
        statuses = [entry.get("status") for entry in attempted]
        if all(status in {"rate limited", "cooling down"} for status in statuses):
            waits = [
                entry.get("humanRemaining")
                for entry in attempted
                if entry.get("humanRemaining") and entry.get("humanRemaining") != "now"
            ]
            wait = waits[0] if waits else None
            return (
                "every model in the chain is out of free quota"
                + (f" and the earliest comes back in {wait}" if wait else "")
                + ", so the local model's own answer was used"
            )
        if all(status == "no key" for status in statuses):
            return "the configured models have no API key, so the local model answered"
        return "the assistant was unavailable, so the local model's answer was used"

    # -- probing ----------------------------------------------------------
    def probe(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Ask one model a trivial question, to answer "is this one usable?".

        Deliberately cheap - a few tokens and no JSON mode - because its purpose
        is to test reachability, quota and the key, not answer quality. The
        headers it returns are the whole point: they say when the model is next
        usable even when the probe fails.
        """
        meta: dict[str, Any] = {"model": spec.get("model"), "status": "unknown"}
        key = self._key_for(spec)
        if not key:
            meta["status"] = "no key"
            meta["detail"] = f"{spec.get('apiKeyEnv') or 'the key variable'} is empty"
            return {"ok": False, **meta, "headers": {}}
        base_url = (spec.get("baseUrl") or OPENROUTER_BASE).rstrip("/")
        try:
            with httpx.Client(base_url=base_url, timeout=min(self.timeout, 20.0)) as client:
                response = client.post(
                    "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "X-Title": "ValueAnchor Copilot",
                    },
                    json={
                        "model": spec.get("model"),
                        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                        "max_tokens": 16,
                    },
                )
        except Exception as error:
            return {"ok": False, "model": spec.get("model"), "status": "unreachable", "detail": str(error)[:180], "headers": {}}

        headers = {name: value for name, value in response.headers.items()}
        if response.status_code == 429:
            source, remedy = self.limit_metadata(response.text)
            return {
                "ok": False,
                "model": spec.get("model"),
                "status": "rate limited",
                "detail": remedy or response.text[:180],
                "limitSource": source,
                "headers": headers,
            }
        if response.status_code >= 400:
            return {
                "ok": False,
                "model": spec.get("model"),
                "status": f"http {response.status_code}",
                "detail": response.text[:180],
                "headers": headers,
            }
        try:
            body = response.json()
        except ValueError:
            return {"ok": False, "model": spec.get("model"), "status": "unreadable response", "headers": headers}
        text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {
            "ok": True,
            "model": spec.get("model"),
            "status": "answered",
            "reply": str(text).strip()[:120],
            "tokens": body.get("usage") or {},
            "headers": headers,
        }
