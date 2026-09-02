"""Render the agent's current state as a standalone HTML page.

The page exists to show one thing that a table of P&L cannot: *why* a candidate
was taken or refused. Raw richness, the macro component shared across the
cohort, and the idiosyncratic remainder are drawn as one bar each, so the
subtraction that drives the whole strategy is visible rather than asserted.

Everything is inlined -- no external assets beyond a webfont -- so the file can
be opened from disk or published as-is.
"""
from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass

from . import cli as cli_mod
from . import execute, gates, state, universe
from .alpaca import Alpaca
from .config import Config
from .termstructure import Kink, TermPoint


# Populated only when collect() is asked to keep them, so the normal path does
# not hold a whole chain in memory for every symbol.
CONTRACTS: dict[str, list] = {}
SPOTS: dict[str, float] = {}


@dataclass
class Row:
    kink: Kink
    allowed: bool
    reasons: list[str]
    tradeable: bool
    qty: int
    max_loss: float


def _curve_svg(points: list[TermPoint], k: Kink | None) -> str:
    """The term structure, with the edge drawn as a measurable distance.

    The numbers alone ask the reader to take the kink on trust. Drawing the
    neighbour-implied level beside the actual one turns the edge into a vertical
    gap you can see -- which is the whole claim of the strategy, so it should be
    the most legible thing on the card.
    """
    if len(points) < 2:
        return ""
    w, h = 300, 150
    left, right, top, bottom = 34, 8, 12, 22
    ivs = [p.atm_iv for p in points]
    lo, hi = min(ivs), max(ivs)
    if k is not None:
        lo, hi = min(lo, k.expected_iv), max(hi, k.expected_iv)
    padv = (hi - lo) * 0.18 or 0.01
    lo, hi = lo - padv, hi + padv
    dtes = [p.dte for p in points]
    x0, x1 = min(dtes), max(dtes)
    xspan = (x1 - x0) or 1

    def px(d: float) -> float:
        return left + (d - x0) / xspan * (w - left - right)

    def py(v: float) -> float:
        return h - bottom - (v - lo) / (hi - lo) * (h - bottom - top)

    line = " ".join(f"{px(p.dte):.1f},{py(p.atm_iv):.1f}" for p in points)

    # y axis: just the two bounds, so the scale is readable without clutter
    grid = "".join(
        f'<line x1="{left}" y1="{py(v):.1f}" x2="{w - right}" y2="{py(v):.1f}" class="gridline"/>'
        f'<text x="{left - 6}" y="{py(v) + 3.5:.1f}" class="ax ax-y">{v:.0%}</text>'
        for v in (lo + padv, hi - padv)
    )
    xlab = "".join(
        f'<text x="{px(d):.1f}" y="{h - 7}" class="ax ax-x">{d}d</text>'
        for d in (x0, x1)
    )

    edge = ""
    if k is not None:
        cx = px(k.rich.dte)
        y_actual, y_expected = py(k.rich.atm_iv), py(k.expected_iv)
        edge = (
            # the level the neighbours imply
            f'<line x1="{cx - 26:.1f}" y1="{y_expected:.1f}" x2="{cx + 26:.1f}" '
            f'y2="{y_expected:.1f}" class="expected"/>'
            # the gap between implied and actual: this is the edge
            f'<rect x="{cx - 3.5:.1f}" y="{min(y_actual, y_expected):.1f}" width="7" '
            f'height="{abs(y_actual - y_expected):.1f}" class="edge-gap"/>'
            f'<circle cx="{cx:.1f}" cy="{y_actual:.1f}" r="4" class="mark-dot"/>'
            f'<text x="{cx + 9:.1f}" y="{y_actual - 6:.1f}" class="ax edge-lab">'
            f'{k.vol_points * 100:+.2f}pts</text>'
        )

    return (
        f'<svg viewBox="0 0 {w} {h}" class="curve" role="img" '
        f'aria-label="term structure with the kink marked">'
        f"{grid}"
        f'<polyline points="{line}" class="curve-line"/>'
        f"{edge}{xlab}</svg>"
    )


def _decomp_bar(k: Kink, scale: float) -> str:
    """Raw richness split into the macro share and the idiosyncratic remainder."""
    raw = max(k.raw_score, 0.0)
    macro = max(min(k.cohort_score, raw), 0.0)
    idio = max(raw - macro, 0.0)
    total = scale or 1.0
    mw = min(macro / total * 100, 100)
    iw = min(idio / total * 100, 100 - mw)
    return (
        '<div class="bar" role="img">'
        f'<span class="bar-macro" style="width:{mw:.1f}%"></span>'
        f'<span class="bar-idio" style="width:{iw:.1f}%"></span>'
        "</div>"
    )


def recent_journal(limit: int = 40) -> list[tuple[str, str, str]]:
    """The last decisions the agent made, newest first.

    Auditability is the claim this project makes loudest, so the evidence for it
    belongs on the page rather than in a directory nobody opens.
    """
    import json

    from .journal import JOURNAL_DIR

    interesting = {
        "refusal": lambda d: (
            f"{d.get('underlying', '')}: " + "; ".join(d.get("reasons", []))[:190]
        ),
        "adjudication": lambda d: (
            f"{d.get('underlying', '')} {d.get('verdict', '')} - "
            f"{str(d.get('reason', ''))[:160]}"
        ),
        "command": lambda d: str(d.get("command", ""))[:200],
        "intent": lambda d: (
            f"{d.get('underlying', '')} {d.get('qty', '')}x @ "
            f"{d.get('limit_price', '')} - {str(d.get('rationale', ''))[:130]}"
        ),
        "manage": lambda d: (
            f"{d.get('underlying', '')}: {str(d.get('reason', ''))[:170]}"
        ),
        "validate": lambda d: f"order {d.get('order_id', '')} cancelled="
                              f"{d.get('cancelled', '')}",
        "scan": lambda d: (
            f"{d.get('raw', 0)} raw kinks, {len(d.get('survivors', []))} idiosyncratic"
        ),
        "cycle": lambda d: str(d.get("status", "")),
        "error": lambda d: str(d.get("error") or d.get("stderr", ""))[:180],
    }

    rows: list[tuple[str, str, str, str]] = []
    if not JOURNAL_DIR.exists():
        return []
    for path in sorted(JOURNAL_DIR.glob("*.jsonl")):
        kind = path.stem.rsplit("-", 3)[0]
        if kind not in interesting:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(d.get("ts", ""))
            try:
                msg = interesting[kind](d)
            except Exception:  # noqa: BLE001
                msg = ""
            if msg:
                rows.append((ts, kind, msg, ts))

    rows.sort(key=lambda r: r[3], reverse=True)
    return [(ts[11:16], kind, msg) for ts, kind, msg, _ in rows[:limit]]


def collect(
    cfg: Config, api: Alpaca, *, keep_contracts: bool = False
) -> tuple[list[Row], dict[str, list[TermPoint]], dict, list]:
    """One chain fetch per underlying: build curves, score kinks, run the gates."""
    from .termstructure import (
        apply_cross_section, build_term_structure, find_kinks, to_contracts,
    )

    today = dt.date.today()
    horizon = (today + dt.timedelta(days=120)).isoformat()
    curves: dict[str, list[TermPoint]] = {}
    found: list[Kink] = []

    for underlying in cfg.universe:
        spot = cli_mod._spot(api, underlying)
        if spot is None:
            continue
        snaps = api.option_chain(
            underlying, expiration_gte=today.isoformat(), expiration_lte=horizon
        )
        contracts = to_contracts(snaps)
        SPOTS[underlying] = spot
        points = build_term_structure(contracts, spot, today)
        if len(points) < 3:
            continue
        curves[underlying] = points
        if keep_contracts:
            CONTRACTS[underlying] = contracts
        found.extend(find_kinks(underlying, points))

    adjusted = apply_cross_section(found)
    survivors = [k for k in adjusted if k.score >= cfg.min_kink_score]

    rows: list[Row] = []
    for k in survivors:
        d = gates.evaluate(k, cfg, open_positions=0, committed_risk_usd=0.0)
        # Mirror the live trade path: the earnings calendar answers this now,
        # so a name is refused for reporting inside the window -- not merely
        # for being a single stock.
        reasons = list(d.reasons)
        tradeable = True
        if not universe.is_tradeable_without_earnings_feed(k.underlying):
            from .earnings import lookup as earnings_lookup

            try:
                info = earnings_lookup(
                    k.underlying, start=today, end=k.rich.expiration
                )
                tradeable = info.safe_to_trade
                if not tradeable:
                    reasons.insert(0, f"earnings risk: {info.describe()}")
            except Exception:  # noqa: BLE001
                tradeable = False
                reasons.insert(0, "earnings risk: calendar check failed")
        rows.append(
            Row(kink=k, allowed=d.allowed and tradeable, reasons=reasons,
                tradeable=tradeable, qty=d.qty, max_loss=d.max_loss_usd)
        )

    try:
        account = execute.account(cfg) if execute.cli_available() else api.account()
    except Exception:  # noqa: BLE001
        account = {}
    try:
        positions = api.positions()
    except Exception:  # noqa: BLE001
        positions = []

    return rows, curves, account, positions


def render(
    cfg: Config,
    rows: list[Row],
    account: dict,
    positions: list,
    curves: dict[str, tuple[list[TermPoint], TermPoint]],
) -> str:
    now = dt.datetime.now(dt.UTC)
    tradeable = [r for r in rows if r.allowed]
    equity = float(account.get("equity") or 0)
    last_equity = float(account.get("last_equity") or 0)
    pnl = equity - last_equity if last_equity else 0.0
    open_trades = state.load()

    deadline_txt = "—"
    if cfg.deadline_utc:
        left = cfg.deadline_utc - now
        hrs = int(left.total_seconds() // 3600)
        mins = int((left.total_seconds() % 3600) // 60)
        deadline_txt = f"{hrs}h {mins}m" if left.total_seconds() > 0 else "passed"

    scale = max([max(r.kink.raw_score, 0.0) for r in rows] or [0.1])

    journal_rows = recent_journal()
    journal_html = "".join(
        f'<li><time>{html.escape(ts)}</time>'
        f'<span class="ev {html.escape(kind)}">{html.escape(kind)}</span>'
        f'<span class="msg">{html.escape(msg)}</span></li>'
        for ts, kind, msg in journal_rows
    ) or '<li><span class="msg">No entries yet.</span></li>' 

    tiles = [
        ("Equity", f"${equity:,.0f}", ""),
        ("Session P&L", f"${pnl:+,.0f}", "pos" if pnl > 0 else ("neg" if pnl < 0 else "")),
        ("Open calendars", str(len(open_trades)), ""),
        ("Candidates", str(len(rows)), ""),
        ("Clear all gates", str(len(tradeable)), "pos" if tradeable else ""),
        ("Deadline in", deadline_txt, ""),
    ]
    tile_html = "".join(
        f'<div class="tile"><span class="tile-k">{html.escape(k)}</span>'
        f'<span class="tile-v {c}">{html.escape(v)}</span></div>'
        for k, v, c in tiles
    )

    card_html = []
    for r in sorted(rows, key=lambda r: (not r.allowed, -r.kink.score)):
        k = r.kink
        pts, mark = curves.get(k.underlying, ([], None))
        inst = universe.classify(k.underlying)
        status = "pass" if r.allowed else "refused"
        label = "TRADEABLE" if r.allowed else "REFUSED"
        reasons = "".join(
            f"<li>{html.escape(x)}</li>" for x in r.reasons
        ) or "<li>all gates clear</li>"
        size = (
            f"{r.qty}× &middot; max loss ${r.max_loss:,.0f}"
            if r.allowed else "&mdash;"
        )
        card_html.append(f"""
        <article class="card {status}">
          <header class="card-head">
            <div>
              <h3>{html.escape(k.underlying)}
                <span class="kind">{html.escape(inst.asset_class)}/{html.escape(inst.kind)}</span>
              </h3>
              <p class="trade">sell {k.rich.dte}d &rarr; buy {k.hedge.dte}d</p>
            </div>
            <span class="pill {status}">{label}</span>
          </header>
          {_curve_svg(pts, k)}
          <dl class="figures">
            <div><dt>Raw</dt><dd>{k.raw_score:+.1%}</dd></div>
            <div><dt>Macro</dt><dd class="macro">{k.cohort_score:+.1%}</dd></div>
            <div><dt>Idiosyncratic</dt><dd class="idio">{k.score:+.1%}</dd></div>
            <div><dt>Vol points</dt><dd>{k.vol_points * 100:+.2f}</dd></div>
          </dl>
          {_decomp_bar(k, scale)}
          <p class="size">{size}</p>
          <ul class="reasons">{reasons}</ul>
        </article>""")

    pos_rows = "".join(
        f"<tr><td>{html.escape(str(p.get('symbol','')))}</td>"
        f"<td class='num'>{html.escape(str(p.get('qty','')))}</td>"
        f"<td class='num'>${float(p.get('unrealized_pl') or 0):,.2f}</td></tr>"
        for p in positions
    ) or "<tr><td colspan='3' class='empty'>No open positions</td></tr>"

    return f"""<title>Kink</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600&display=swap">
<style>
  :root {{
    --ground:#f4f6f8; --surface:#ffffff; --surface-2:#fafbfc;
    --ink:#11151b; --ink-soft:#5b6572; --ink-faint:#8c95a1;
    --rule:#dee3e9;
    --macro:#8a94a6; --idio:#b4690e;
    --pass:#1b7f5a; --refuse:#a2453f;
    --shadow:0 1px 2px rgba(17,21,27,.06), 0 8px 24px rgba(17,21,27,.05);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground:#0d1015; --surface:#161a21; --surface-2:#1b2029;
      --ink:#e7eaef; --ink-soft:#9aa4b1; --ink-faint:#6d7784;
      --rule:#272d37;
      --macro:#7b8494; --idio:#d78d2b;
      --pass:#3fa87c; --refuse:#c76a63;
      --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
    }}
  }}
  :root[data-theme="dark"] {{
    --ground:#0d1015; --surface:#161a21; --surface-2:#1b2029;
    --ink:#e7eaef; --ink-soft:#9aa4b1; --ink-faint:#6d7784;
    --rule:#272d37;
    --macro:#7b8494; --idio:#d78d2b;
    --pass:#3fa87c; --refuse:#c76a63;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }}

  body {{
    background:var(--ground); color:var(--ink);
    font-family:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
    line-height:1.55; margin:0; padding:clamp(20px,4vw,48px);
  }}
  .wrap {{ max-width:1120px; margin:0 auto; display:flex; flex-direction:column; gap:36px; }}

  header.top h1 {{
    font-family:"IBM Plex Serif",Georgia,serif; font-weight:600;
    font-size:clamp(30px,4vw,42px); margin:0 0 6px; letter-spacing:-.015em;
    text-wrap:balance;
  }}
  header.top p {{ margin:0; color:var(--ink-soft); max-width:64ch; }}
  .stamp {{
    font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12px;
    color:var(--ink-faint); letter-spacing:.04em; text-transform:uppercase;
    margin-bottom:10px;
  }}

  .tiles {{ display:flex; flex-wrap:wrap; gap:1px;
           background:var(--rule); border:1px solid var(--rule); border-radius:8px; overflow:hidden; }}
  .tile {{ background:var(--surface); padding:14px 16px; display:flex; flex-direction:column;
          gap:4px; flex:1 1 150px; }}
  .tile-k {{ font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--ink-faint); }}
  .tile-v {{ font-family:"IBM Plex Mono",monospace; font-size:22px; font-variant-numeric:tabular-nums; }}
  .tile-v.pos {{ color:var(--pass); }} .tile-v.neg {{ color:var(--refuse); }}

  h2 {{ font-family:"IBM Plex Serif",Georgia,serif; font-size:20px; margin:0 0 4px; }}
  .lede {{ color:var(--ink-soft); margin:0 0 18px; max-width:68ch; font-size:15px; }}

  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; }}
  .card {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px;
          padding:16px; box-shadow:var(--shadow); display:flex; flex-direction:column; gap:12px; }}
  .card.pass {{ border-left:3px solid var(--pass); }}
  .card.refused {{ border-left:3px solid var(--refuse); }}
  .card-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }}
  .card h3 {{ margin:0; font-size:17px; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }}
  .kind {{ font-size:11px; color:var(--ink-faint); font-weight:400; letter-spacing:.03em; }}
  .trade {{ margin:2px 0 0; font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); }}
  .pill {{ font-size:10px; letter-spacing:.08em; padding:3px 8px; border-radius:20px;
          white-space:nowrap; font-weight:500; }}
  .pill.pass {{ background:color-mix(in srgb,var(--pass) 14%,transparent); color:var(--pass); }}
  .pill.refused {{ background:color-mix(in srgb,var(--refuse) 14%,transparent); color:var(--refuse); }}

  .curve {{ width:100%; height:auto; display:block; }}
  .curve-line {{ fill:none; stroke:var(--ink); stroke-width:1.7; stroke-linejoin:round;
                stroke-linecap:round; }}
  .gridline {{ stroke:var(--rule); stroke-width:1; }}
  .ax {{ font-family:"IBM Plex Mono",monospace; font-size:9px; fill:var(--ink-faint); }}
  .ax-y {{ text-anchor:end; }}
  .ax-x {{ text-anchor:middle; }}
  .expected {{ stroke:var(--macro); stroke-width:1.4; stroke-dasharray:4 3; }}
  .edge-gap {{ fill:var(--idio); opacity:.55; }}
  .mark-dot {{ fill:var(--idio); }}
  .edge-lab {{ fill:var(--idio); font-size:9.5px; font-weight:500; }}

  .figures {{ display:grid; grid-template-columns:1fr 1fr; gap:8px 12px; margin:0; }}
  .figures div {{ display:flex; justify-content:space-between; gap:8px;
                 border-bottom:1px dotted var(--rule); padding-bottom:3px; }}
  .figures dt {{ font-size:12px; color:var(--ink-soft); }}
  .figures dd {{ margin:0; font-family:"IBM Plex Mono",monospace; font-size:13px;
                font-variant-numeric:tabular-nums; }}
  .figures dd.macro {{ color:var(--macro); }}
  .figures dd.idio {{ color:var(--idio); font-weight:500; }}

  .bar {{ display:flex; height:7px; border-radius:4px; overflow:hidden;
         background:color-mix(in srgb,var(--ink-faint) 14%,transparent); }}
  .bar-macro {{ background:var(--macro); }}
  .bar-idio {{ background:var(--idio); }}

  .size {{ margin:0; font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-soft); }}
  .reasons {{ margin:0; padding-left:16px; font-size:12.5px; color:var(--ink-soft); }}
  .reasons li {{ margin-bottom:2px; }}

  .legend {{ display:flex; gap:18px; flex-wrap:wrap; font-size:12.5px; color:var(--ink-soft);
            margin-bottom:14px; }}
  .legend span {{ display:inline-flex; align-items:center; gap:6px; }}
  .swatch {{ width:11px; height:11px; border-radius:2px; display:inline-block; }}

  .panel {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px;
           overflow:hidden; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:10px 14px; border-bottom:1px solid var(--rule); }}
  th {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink-faint);
       background:var(--surface-2); font-weight:500; }}
  td.num {{ font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; text-align:right; }}
  td.empty {{ color:var(--ink-faint); text-align:center; }}
  tr:last-child td {{ border-bottom:none; }}
  .scroll {{ overflow-x:auto; }}

  .log {{ font-family:"IBM Plex Mono",monospace; font-size:12px; margin:0;
         max-height:340px; overflow:auto; }}
  .log li {{ display:grid; grid-template-columns:52px 84px 1fr; gap:10px;
            padding:7px 14px; border-bottom:1px solid var(--rule); align-items:baseline; }}
  .log li:last-child {{ border-bottom:none; }}
  .log time {{ color:var(--ink-faint); font-variant-numeric:tabular-nums; }}
  .log .ev {{ font-size:10px; letter-spacing:.05em; text-transform:uppercase;
             color:var(--ink-faint); }}
  .log .ev.refusal {{ color:var(--refuse); }}
  .log .ev.command {{ color:var(--idio); }}
  .log .msg {{ color:var(--ink-soft); overflow-wrap:anywhere; }}

  .note {{ border-left:3px solid var(--idio); padding:2px 0 2px 16px; color:var(--ink-soft);
          max-width:70ch; }}
  .note strong {{ color:var(--ink); font-weight:600; }}
  footer {{ color:var(--ink-faint); font-size:12.5px; border-top:1px solid var(--rule);
           padding-top:16px; }}
</style>

<div class="wrap">
  <header class="top">
    <p class="stamp">{now:%Y-%m-%d %H:%M} UTC &middot; alpaca paper &middot; account {html.escape(str(account.get('id','—'))[:8])}</p>
    <h1>Trading the shape, not the direction</h1>
    <p>Kink sells an expiration whose implied volatility is richer than its neighbours
       imply &mdash; but only the part of that richness the rest of its asset class does
       not share. What every name shows at once is a scheduled macro event, and selling
       it is selling event premium. What one name shows alone is the trade.</p>
  </header>

  <section class="tiles">{tile_html}</section>

  <section>
    <h2>Candidates</h2>
    <p class="lede">Every kink found this scan, with the decomposition that decided it
       and every reason it was refused.</p>
    <div class="legend">
      <span><i class="swatch" style="background:var(--macro)"></i> macro component &mdash; shared across the cohort, discarded</span>
      <span><i class="swatch" style="background:var(--idio)"></i> idiosyncratic remainder &mdash; the tradeable part</span>
    </div>
    <div class="grid">{"".join(card_html)}</div>
  </section>

  <section>
    <h2>Positions</h2>
    <div class="panel scroll">
      <table>
        <thead><tr><th>Contract</th><th class="num">Qty</th><th class="num">Unrealised</th></tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Decision journal</h2>
    <p class="lede">Every scan, refusal, adjudication and order the agent has made,
       newest first &mdash; including the literal Alpaca CLI commands, so the account
       can be replayed without reading any Python.</p>
    <div class="panel"><ul class="log">{journal_html}</ul></div>
  </section>

  <section class="note">
    <p><strong>On the P&amp;L below this line.</strong> This account has traded for days,
    not months. Whatever number it shows is one draw from a wide distribution, and it is
    not evidence of skill either way. The claim being made here is about the decision
    process &mdash; every candidate, every refusal and every order is journalled and
    reproducible &mdash; not about the terminal equity.</p>
  </section>

  <footer>
    Kink &middot; term-structure options agent on Alpaca paper trading &middot;
    strategy, gates and exits are deterministic; the language model can only veto.
  </footer>
</div>
"""


def build(cfg: Config, api: Alpaca, out_path: str) -> str:
    rows, curves, account, positions = collect(cfg, api)
    marked = {
        r.kink.underlying: (curves.get(r.kink.underlying, []), r.kink.rich)
        for r in rows
    }
    page = render(cfg, rows, account, positions, marked)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out_path
