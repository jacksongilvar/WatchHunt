import html
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus

CONCERN_COLOR = {"none_visible": "#2e7d32", "some": "#ef6c00", "high": "#c62828", "cannot_assess": "#757575"}
CONCERN_LABEL = {"none_visible": "No visible red flags (not proof)", "some": "Some concerns",
                 "high": "High concern", "cannot_assess": "Cannot assess from photos"}


def _e(x):
    return html.escape(str(x)) if x is not None else ""


def _comp_query(lst):
    ai = lst.ai or {}
    parts = [ai.get("brand"), ai.get("model")] if ai.get("brand") else [lst.brand_hint]
    q = " ".join(p for p in parts if p) or lst.title[:60]
    return q


def _card(lst):
    ai = lst.ai or {}
    img = f'<img src="{_e(lst.image_urls[0])}" style="width:140px;height:140px;object-fit:cover;border-radius:6px">' \
        if lst.image_urls else ""
    bids = f" | {lst.bids} bids" if lst.bids is not None else ""
    left = f" | {_e(lst.time_left)}" if lst.time_left else (f" | ends {_e(lst.end_time)[:16]}" if lst.end_time else "")
    q = quote_plus(_comp_query(lst))
    comps_links = (f'<a href="https://www.ebay.com/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1">eBay sold</a> · '
                   f'<a href="https://www.chrono24.com/search/index.htm?query={q}">Chrono24 asking</a>')

    ai_html = ""
    if ai:
        concern = ai.get("authenticity_concern", "cannot_assess")
        ident = " ".join(x for x in [ai.get("brand"), ai.get("model"), ai.get("reference_guess")] if x) or "Unidentified"
        flags = "".join(f"<li>{_e(f)}</li>" for f in ai.get("visible_red_flags", []) or [])
        qs = "".join(f"<li>{_e(x)}</li>" for x in ai.get("questions_for_seller", []) or [])
        val = ai.get("rough_value_range_usd")
        val_txt = f"${val[0]:,.0f} to ${val[1]:,.0f} (AI guess, verify with sold comps)" \
            if isinstance(val, list) and len(val) == 2 and all(isinstance(v, (int, float)) for v in val) else "n/a"
        ai_html = f"""
        <div style="margin-top:6px"><b>AI read:</b> {_e(ident)}
          ({_e(ai.get('identification_confidence', '?'))} confidence, {_e(ai.get('era_guess') or 'era unknown')},
          photos {_e(ai.get('photo_quality', '?'))})</div>
        <div style="color:{CONCERN_COLOR.get(concern, '#757575')};font-weight:bold">
          {_e(CONCERN_LABEL.get(concern, concern))}</div>
        <div>{_e(ai.get('summary', ''))}</div>
        {f'<div><b>Red flags:</b><ul style="margin:2px 0">{flags}</ul></div>' if flags else ''}
        {f'<div><b>Ask the seller:</b><ul style="margin:2px 0">{qs}</ul></div>' if qs else ''}
        <div><b>Rough value:</b> {val_txt}</div>"""

    comps_html = ""
    c = lst.comps
    if c and c.get("count"):
        comps_html = (f'<div><b>ShopGoodwill sold (90d, "{_e(c["query"])}"):</b> {c["count"]} results, '
                      f'median ${c["median"]:,.0f}, range ${c["low"]:,.0f} to ${c["high"]:,.0f}. '
                      f'Loose keyword match, check the listings.</div>')

    return f"""
    <table style="width:100%;border-bottom:1px solid #ddd;padding:10px 0"><tr>
      <td style="width:150px;vertical-align:top">{img}</td>
      <td style="vertical-align:top;font:14px/1.4 -apple-system,Helvetica,Arial">
        <div style="font-size:16px"><a href="{_e(lst.url)}">{_e(lst.title)}</a></div>
        <div style="color:#555">{_e(lst.source)} | ${lst.price:,.2f}{bids}{left} | score {lst.score}</div>
        <div style="color:#555;font-size:12px">Why flagged: {_e('; '.join(lst.reasons))}</div>
        {ai_html}{comps_html}
        <div style="margin-top:4px;font-size:12px">Comps: {comps_links}</div>
      </td></tr></table>"""


def build(ai_checked, text_only, stats):
    ai_checked = sorted(ai_checked, key=lambda l: (not (l.ai or {}).get("worth_a_closer_look", False), -l.score))
    text_only = sorted(text_only, key=lambda l: -l.score)
    now = datetime.now().strftime("%a %b %d, %I:%M %p")
    stats_line = " | ".join(f"{k}: {v}" for k, v in stats.items())
    return f"""<html><body style="max-width:820px;margin:auto;font:14px -apple-system,Helvetica,Arial">
    <h2>Watch Hunter digest, {now}</h2>
    <p style="color:#555">{_e(stats_line)}</p>
    <p style="background:#fff3e0;padding:8px;border-radius:6px">AI output is triage only.
    Nothing here is authenticated. Check sold comps, fees and service cost before bidding.</p>
    <h3>AI checked ({len(ai_checked)})</h3>{''.join(_card(l) for l in ai_checked) or '<p>None this run.</p>'}
    <h3>Flagged by text only ({len(text_only)})</h3>{''.join(_card(l) for l in text_only) or '<p>None.</p>'}
    </body></html>"""


def write(html_doc, out_dir="digests"):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"digest-{datetime.now():%Y-%m-%d-%H%M}.html")
    with open(path, "w") as f:
        f.write(html_doc)
    return path


def email(html_doc, subject):
    host, to = os.getenv("SMTP_HOST"), os.getenv("DIGEST_TO")
    if not host or not to:
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, os.getenv("SMTP_USER", to), to
    msg.attach(MIMEText(html_doc, "html"))
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
        s.send_message(msg)
    return True
