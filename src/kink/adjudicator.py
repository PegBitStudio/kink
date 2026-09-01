"""The model's one job: decide whether a surviving kink has a known catalyst.

After the cross-section strips the macro calendar out, what remains is richness
specific to a single underlying. There are two reasons that happens:

  1. A dated, name-specific event lives inside that expiration -- earnings, a
     court date, an FDA decision, a shareholder vote. The premium is fair. Being
     short it is being short the event, which is exactly the trade we refuse.
  2. Nothing in particular. The premium is a mispricing, and it is what we want.

Distinguishing those is not a maths problem. It is a question about the world,
and it is the one part of this system where a language model genuinely knows
something the code cannot derive from the chain.

The model is deliberately the least-trusted component here:

  * It can only ever VETO. There is no code path by which the model causes a
    trade to happen that the deterministic layer had not already approved.
  * It cannot size, price, choose strikes, or place orders.
  * Every failure mode -- timeout, malformed JSON, missing key, unparseable
    verdict -- resolves to VETO. Fail-closed, always.

So the worst a broken or hostile model can do to this account is stop it from
trading.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

import requests

from .journal import record
from .evidence import Evidence
from .termstructure import Kink

Verdict = Literal["TRADE", "VETO", "ABSTAIN"]

# The adjudicator needs one thing: an OpenAI-compatible /chat/completions
# endpoint. Featherless, Groq, OpenRouter, Together, Gemini's compat layer and a
# local llama.cpp server all speak it, so the provider is configuration rather
# than a code dependency. Set LLM_BASE_URL + LLM_API_KEY + ADJUDICATOR_MODEL.
DEFAULT_BASE_URL = "https://api.featherless.ai/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b"

# Providers we have URLs for, so `kink providers` can print them.
KNOWN_PROVIDERS = {
    "featherless": "https://api.featherless.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "local": "http://localhost:8080/v1",
}


def resolve_endpoint() -> tuple[str, str, str]:
    """Return (url, api_key, model) from the environment.

    LLM_API_KEY is preferred; FEATHERLESS_API_KEY is still honoured so an
    existing .env keeps working.
    """
    base = os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
    base = base.rstrip("/")
    if not base.endswith("/chat/completions"):
        base = f"{base}/chat/completions"
    key = (
        os.getenv("LLM_API_KEY", "").strip()
        or os.getenv("FEATHERLESS_API_KEY", "").strip()
    )
    model = os.getenv("ADJUDICATOR_MODEL", DEFAULT_MODEL)
    return base, key, model

# Framing matters more than it looks. Asked to review a *trade*, safety-tuned
# models refuse the whole question as investment advice (gpt-oss-120b does
# exactly this). Asked to report what is on a company's calendar -- a matter of
# fact, not opinion -- the same models answer. So the prompt asks only the
# factual half, and the trading decision stays in code where it belongs.
SYSTEM_PROMPT = """\
You are a document reader. You are given a ticker, a date window, and a dossier
of retrieved facts: dated corporate actions on file, and recent news headlines.

Read ONLY the dossier. Do not use anything you remember about this company --
your training data does not cover this window, and the dossier does.

Decide whether the dossier shows a scheduled, dated, company-specific event
falling inside the window. Qualifying events: an earnings report, an FDA
decision date, a court ruling, a merger or shareholder vote, an index
reconstitution, a scheduled product launch, or a lockup expiry.

Routine cash dividends and ex-dividend dates do NOT qualify -- they are
predictable and already priced.
Market-wide macro events (payrolls, CPI, FOMC) do NOT qualify.
Speculation, analyst opinions and rumours in headlines do NOT qualify; only a
scheduled event with a date does.

Answer ONLY with JSON in exactly this form:
{"verdict": "TRADE" | "VETO" | "ABSTAIN", "reason": "<one sentence>"}

VETO    -- the dossier shows a qualifying dated event inside the window.
ABSTAIN -- the dossier is ambiguous, or hints at an event without a clear date.
TRADE   -- the dossier shows no qualifying dated event inside the window.

Cite what you saw in the dossier. Do not cite your own knowledge.\
"""


@dataclass(frozen=True)
class Ruling:
    verdict: Verdict
    reason: str
    model: str

    @property
    def allows_trade(self) -> bool:
        """Only an explicit TRADE proceeds. Everything else refuses."""
        return self.verdict == "TRADE"


def _refuse(reason: str, model: str = "none") -> Ruling:
    return Ruling(verdict="VETO", reason=reason, model=model)


def describe_candidate(kink: Kink, today: str, dossier: str) -> str:
    return (
        f"Window: {today} through {kink.rich.expiration}\n\n"
        f"--- DOSSIER ---\n{dossier}\n--- END DOSSIER ---\n\n"
        f"Does the dossier show a qualifying dated event inside the window?"
    )


def adjudicate(
    kink: Kink, *, today: str, ev: Evidence | None = None, timeout: int = 30
) -> Ruling:
    url, api_key, model = resolve_endpoint()

    if not api_key:
        return _refuse("LLM_API_KEY not set; refusing rather than trading blind")
    if ev is None:
        return _refuse("no evidence gathered; refusing rather than trading blind", model)
    if not ev.complete:
        # Partial evidence must never become a TRADE: an event we failed to
        # retrieve looks exactly like an event that does not exist.
        return _refuse(f"evidence incomplete ({'; '.join(ev.errors)})", model)

    prompt = describe_candidate(kink, today, ev.render())
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 1200,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return _refuse(f"adjudicator unreachable ({type(exc).__name__})", model)

    if resp.status_code >= 400:
        return _refuse(f"adjudicator HTTP {resp.status_code}", model)

    try:
        content = resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError):
        return _refuse("adjudicator response had no message content", model)

    ruling = parse_ruling(content, model)
    record(
        "adjudication",
        {
            "underlying": kink.underlying,
            "expiration": str(kink.rich.expiration),
            "idio_score": kink.score,
            "verdict": ruling.verdict,
            "reason": ruling.reason,
            "model": model,
            "raw_response": content[:400],
        },
    )
    return ruling


def parse_ruling(content: str, model: str) -> Ruling:
    """Parse the model's JSON. Anything unexpected is a refusal, not a guess."""
    text = content.strip()
    # Reasoning models emit a <think> block before the answer. Drop it -- the
    # verdict is what is being parsed, not the deliberation.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return _refuse("adjudicator did not return JSON", model)

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return _refuse("adjudicator returned malformed JSON", model)

    verdict = str(data.get("verdict", "")).upper().strip()
    reason = str(data.get("reason", "")).strip() or "no reason given"

    if verdict not in ("TRADE", "VETO", "ABSTAIN"):
        return _refuse(f"adjudicator returned unknown verdict {verdict!r}", model)

    return Ruling(verdict=verdict, reason=reason, model=model)  # type: ignore[arg-type]
