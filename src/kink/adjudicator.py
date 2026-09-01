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
from .termstructure import Kink

Verdict = Literal["TRADE", "VETO", "ABSTAIN"]

# The adjudicator needs one thing: an OpenAI-compatible /chat/completions
# endpoint. Featherless, Groq, OpenRouter, Together, Gemini's compat layer and a
# local llama.cpp server all speak it, so the provider is configuration rather
# than a code dependency. Set LLM_BASE_URL + LLM_API_KEY + ADJUDICATOR_MODEL.
DEFAULT_BASE_URL = "https://api.featherless.ai/v1"
DEFAULT_MODEL = "zai-org/GLM-5.2"

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

SYSTEM_PROMPT = """\
You are a risk reviewer on an options desk. You are shown one candidate trade.

The desk has found that implied volatility for a single expiration on one
underlying is richer than the neighbouring expirations, AFTER subtracting the
market-wide richness at that same expiration across a universe of other names.
So a scheduled macro event (payrolls, CPI, FOMC) has already been accounted for
and is NOT an explanation you should offer.

The desk wants to SELL that expiration and BUY a longer-dated one. That means
the desk is SHORT whatever happens inside the rich expiration.

Your only question: is there a known, dated, company-or-sector-specific event
falling inside that window which would justify the extra premium?

Examples that justify it: an earnings report, an FDA decision, a court ruling,
a merger vote, an index reconstitution, a scheduled product launch, a lockup
expiry.

Answer ONLY with JSON in exactly this form:
{"verdict": "TRADE" | "VETO" | "ABSTAIN", "reason": "<one sentence>"}

VETO   -- you can name a specific dated event inside the window.
ABSTAIN -- you are unsure, or your knowledge of this name is out of date.
TRADE  -- you know of no dated event that would explain the richness.

Be honest about uncertainty. ABSTAIN is treated as a refusal, and refusing a
good trade costs this desk far less than selling premium in front of an event
it did not see coming.\
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


def describe_candidate(kink: Kink, today: str) -> str:
    return (
        f"Today is {today}.\n"
        f"Underlying: {kink.underlying}\n"
        f"Rich expiration: {kink.rich.expiration} ({kink.rich.dte} days out), "
        f"ATM implied vol {kink.rich.atm_iv:.1%}\n"
        f"Neighbouring expirations imply {kink.expected_iv:.1%} there.\n"
        f"Raw richness: +{kink.raw_score:.1%}\n"
        f"Shared by the rest of the universe at this expiration: "
        f"+{kink.cohort_score:.1%}\n"
        f"Richness specific to {kink.underlying}: +{kink.score:.1%}\n"
        f"Proposed: sell the {kink.rich.dte}-day expiration, buy the "
        f"{kink.hedge.dte}-day expiration ({kink.hedge.expiration}).\n"
        f"Is there a dated {kink.underlying}-specific event on or before "
        f"{kink.rich.expiration}?"
    )


def adjudicate(kink: Kink, *, today: str, timeout: int = 30) -> Ruling:
    url, api_key, model = resolve_endpoint()

    if not api_key:
        return _refuse("LLM_API_KEY not set; refusing rather than trading blind")

    prompt = describe_candidate(kink, today)
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
                "max_tokens": 200,
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
