"""What it costs to get a watch ready to sell, job by job.

A watch gets a list of jobs (battery, crystal, strap, full service...). The list comes from, in order of trust:
  1. Your watchlist entry: `service: <dollars>` (a fixed total) or `jobs: [battery, crystal]`
  2. The AI check's `service_needs` list, judged from the photos and listing text
  3. Keywords in the AI's condition notes and the listing text (for watches checked before service_needs existed)
  4. A default for the watch type when nothing says otherwise (a quartz watch gets a battery)

Each job is priced by brand tier (config brands.<name>.tier): 1 everyday brands (Seiko, Citizen, Hamilton,
unknown brands), 2 mid Swiss (Omega, Longines, TAG), 3 luxury (Rolex, Cartier, Patek). A crystal on a Seiko
is not a crystal on a Cartier.
"""
import re

# Watch types, from the AI's movement type plus words in the listing.
SOLAR = re.compile(r"eco[- ]?drive|solar|tough solar", re.I)
KINETIC = re.compile(r"kinetic|auto[- ]?quartz|autoquartz", re.I)
PERPETUAL = re.compile(r"perpetual calendar|perpetual|8f3[2-5]|8f56", re.I)
CHRONO = re.compile(r"chronograph|chrono\b", re.I)

# Condition keywords, read from the AI's notes and the listing text.
K = {
    "crystal": re.compile(r"crack\w*[^.;]{0,30}crystal|crystal[^.;]{0,30}crack|chipped crystal|crystal[^.;]{0,20}(chip|shatter|broken)"
                          r"|deep scratch[^.;]{0,30}crystal|crystal[^.;]{0,30}deep scratch", re.I),
    "strap": re.compile(r"head only|watch head|no strap|no band|no bracelet|missing (strap|band|bracelet)|without (a )?(strap|band)"
                        r"|strap (is )?(cracked|broken|torn)|cracked[^.;]{0,15}strap", re.I),
    "crown_stem": re.compile(r"missing crown|crown (is )?missing|no crown|broken stem|stem (is )?broken|crown (is )?loose", re.I),
    "broken": re.compile(r"for (parts|repair)|parts or repair|not working|doesn'?t (run|work)|does not (run|work)|won'?t (run|wind)"
                         r"|stops|needs service|needs (a )?repair|non[- ]?working|broken|as[- ]is for parts", re.I),
    "untested": re.compile(r"untested|not tested|unknown if|condition unknown|running condition (is )?unknown", re.I),
    "no_run_quartz": re.compile(r"needs (a )?(new )?batter|dead batter|batter(y|ies) (is )?(dead|needed)|not running|untested|not tested", re.I),
    "solar_weak": re.compile(r"capacitor|secondary cell|rechargeable cell|ticks? (in)?consistent|2[- ]second|skip(s|ping)"
                             r"|not holding (a )?charge|won'?t hold", re.I),
}


BOILERPLATE = re.compile(r"and/or|\(if included\)|not tested for function|may (show|require|have)|items may|sold as[- ]is"
                         r"|no returns|please (see|review)|refer to (the )?photos|unless (otherwise )?(noted|stated)", re.I)
SERVICED = re.compile(r"(recently |fully )?serviced|overhauled|cleaned and (oiled|regulated)", re.I)


def _tier(lst, brands):
    ai = lst.ai or {}
    name = (ai.get("brand") or lst.brand_hint or "").lower()
    for key, b in (brands or {}).items():
        if name and (name == key or key in name):
            return max(1, min(3, int(b.get("tier", 1) or 1)))
    return 1


def _price(cfg, job, tier):
    j = (cfg.get("jobs") or {}).get(job) or {}
    cost = j.get("cost", 0)
    if isinstance(cost, list):
        cost = cost[min(tier, len(cost)) - 1] if cost else 0
    return float(cost), j.get("label", job.replace("_", " "))


def watch_type(lst):
    """quartz, perpetual, solar, kinetic, automatic, manual, chrono_auto, chrono_quartz or unknown."""
    ai = lst.ai or {}
    text = " ".join([lst.title or "", (lst.description or "")[:800], ai.get("model") or "", ai.get("summary") or ""])
    mv = (ai.get("movement_type") or "unknown").lower()
    if KINETIC.search(text):
        return "kinetic"
    if SOLAR.search(text):
        return "solar"
    chrono = bool(CHRONO.search(text))
    if mv == "quartz" or (mv == "unknown" and re.search(r"\bquartz\b", text, re.I)):
        if PERPETUAL.search(text):
            return "perpetual"
        return "chrono_quartz" if chrono else "quartz"
    if mv in ("automatic", "manual"):
        return "chrono_auto" if chrono else mv
    return "unknown"


def _infer(lst, wtype, tier=1):
    """Jobs read from the AI's condition notes and the listing text, for checks without service_needs."""
    ai = lst.ai or {}
    # Observations only: the listing, the AI's condition notes and red flags. The AI summary is left out because it
    # speculates ("capacitor health is the main risk"), and seller boilerplate is dropped sentence by sentence
    # (Goodwill lists "scratches and/or cracks on crystal (if included)" on every watch).
    raw = " ".join([lst.title or "", (lst.description or "")[:1500], ai.get("condition_notes") or "",
                    ". ".join(ai.get("visible_red_flags") or [])])
    text = " ".join(x for x in re.split(r"(?<=[.!?;])\s+|\n+", raw) if not BOILERPLATE.search(x))
    jobs = []
    broken = bool(K["broken"].search(text))
    if wtype in ("quartz", "chrono_quartz", "perpetual"):
        jobs.append("battery")  # Goodwill and estate watches almost never come with a fresh battery
        if wtype == "perpetual":
            jobs.append("calendar_reset")
        if broken and not K["no_run_quartz"].search(text):
            jobs.append("quartz_movement")
    elif wtype == "solar":
        if K["solar_weak"].search(text):
            jobs.append("solar_cell")
    elif wtype == "kinetic":
        jobs.append("kinetic_cell")  # their capacitors are almost always worn out by now
    elif wtype in ("automatic", "manual", "chrono_auto", "unknown"):
        if broken:
            jobs.append("full_service_chrono" if wtype == "chrono_auto" else "full_service")
        elif K["untested"].search(text) or wtype == "unknown" or (tier >= 2 and not SERVICED.search(text)):
            # Mid and luxury buyers expect a mechanical watch to have been serviced; budget buyers do not.
            jobs.append("clean_regulate")
    if K["crystal"].search(text):
        jobs.append("crystal")
    if K["strap"].search(text):
        jobs.append("strap")
    if K["crown_stem"].search(text):
        jobs.append("crown_stem")
    return jobs


def estimate(lst, svc_cfg, brands=None):
    """{"total", "jobs": [{"job", "label", "cost"}], "basis", "type", "tier"}."""
    svc_cfg = svc_cfg or {}
    man = lst.manual or {}
    tier = _tier(lst, brands)
    wtype = watch_type(lst)
    if man.get("service") is not None and not man.get("jobs"):
        total = float(man["service"])
        return {"total": total, "jobs": [{"job": "manual", "label": "Your estimate", "cost": total}] if total else [],
                "basis": "your watchlist entry", "type": wtype, "tier": tier}
    known = set((svc_cfg.get("jobs") or {}).keys())
    ai = lst.ai or {}
    if man.get("jobs"):
        jobs, basis = list(man["jobs"]), "your watchlist entry"
    elif isinstance(ai.get("service_needs"), list) and not ai.get("check_failed"):
        jobs, basis = [j for j in ai["service_needs"] if j in known], "AI check of the photos"
        # A quartz watch from a thrift or estate seller needs a battery even when the AI forgets to say so.
        if wtype in ("quartz", "chrono_quartz", "perpetual") and "battery" not in jobs and "quartz_movement" not in jobs:
            jobs.insert(0, "battery")
        if wtype == "perpetual" and "battery" in jobs and "calendar_reset" not in jobs:
            jobs.append("calendar_reset")
    else:
        jobs, basis = _infer(lst, wtype, tier), "condition notes and listing text"
    seen, out = set(), []
    for j in jobs:
        if j in seen or j not in known:
            continue
        seen.add(j)
        cost, label = _price(svc_cfg, j, tier)
        out.append({"job": j, "label": label, "cost": cost})
    return {"total": round(sum(x["cost"] for x in out), 2), "jobs": out, "basis": basis, "type": wtype, "tier": tier}


def jobs_prompt(svc_cfg):
    """The job list for the AI's system prompt."""
    jobs = (svc_cfg or {}).get("jobs") or {}
    lines = [f"- {jid}: {j.get('ask', j.get('label', jid))}" for jid, j in jobs.items()]
    return ("\n\nservice_needs: list the jobs this watch needs before it can be resold, using only these ids. "
            "Judge from the photos and listing text. Empty list if it looks ready to sell. Do not add a job just "
            "in case; the tool adds a battery to every quartz watch itself.\n" + "\n".join(lines))
