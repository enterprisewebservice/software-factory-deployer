"""The daily progress digest: one email a day to the workshop owner, from the
same ledger the admin page shows — who showed up in the last 24 hours, where they
are, how long they have spent, what the sweep saw them finish, and who looks stuck.
Sent once per UTC day after DIGEST_HOUR_UTC (marker `_digest` in factory-progress);
POST /api/admin/digest sends one now."""
import json, os, smtplib, ssl, threading, time, urllib.parse
import datetime as dt
from email.message import EmailMessage
import helpdesk as HD
import progress as P

HOUR = int(os.environ.get("DIGEST_HOUR_UTC", "13"))
ADMIN_URL = os.environ.get("DIGEST_ADMIN_URL", "https://factory.apps.salamander.aimlworkbench.com/hub/admin.html")
MODULE_MIN_STUCK = 45 * 60


def _age(iso):
    t = P.epoch(iso or "")
    return None if t is None else time.time() - t


def _ago(iso):
    a = _age(iso)
    if a is None: return "never"
    if a < 3600: return f"{int(a // 60)} min ago"
    if a < 86400: return f"{a / 3600:.0f} h ago"
    return f"{a / 86400:.0f} d ago"


def _dur(sec):
    sec = int(sec or 0)
    return f"{sec}s" if sec < 60 else (f"{sec // 60}m" if sec < 3600 else f"{sec // 3600}h {(sec % 3600) // 60}m")


def compose(rows, now=None):
    now = now or time.time()
    day = 86400
    active = [r for r in rows if (_age(r.get("last_seen")) or 1e9) <= day
              or any((_age(c.get("first_done")) or 1e9) <= day for c in (r.get("checks") or {}).values())]
    quiet = [r for r in rows if r.get("last_seen") and _age(r.get("last_seen")) > 7 * day]
    never = [r for r in rows if not r.get("last_seen") and (_age(r.get("ready")) or 0) > day]
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%a %d %b %Y")
    L = [f"Workshop digest, {stamp} (UTC)", "", f"Active in the last 24 h: {len(active)} of {len(rows)} seats."]
    stuck = []
    for r in active:
        cur = r.get("current") or {}
        mods = r.get("modules") or {}
        by_mod = ", ".join(f"M{k} {_dur(v['seconds'])}" if k.isdigit() else f"{v['label']} {_dur(v['seconds'])}"
                           for k, v in sorted(mods.items(), key=lambda kv: (not kv[0].isdigit(), kv[0])) if v.get("seconds", 0) >= 60)
        new_done = [n for n, c in (r.get("checks") or {}).items() if (_age(c.get("first_done")) or 1e9) <= day]
        done = ", ".join(f"M{n}" + (" (new)" if n in new_done else "") for n in r.get("done") or []) or "none yet"
        errs = r.get("errors") or []
        L += ["", f"- {r.get('username')} ({r.get('brand')}) — last seen {_ago(r.get('last_seen'))}"
              + (f" — now on {cur.get('label')}" if cur else ""),
              f"    time: {_dur(r.get('total_seconds'))} total" + (f" ({by_mod})" if by_mod else ""),
              f"    done per the sweep: {done}" + (f"; looks wrong: M{', M'.join(r['wrong'])}" if r.get("wrong") else "")]
        if errs:
            L.append(f"    errors ({len(errs)}): " + " | ".join(e["text"][:120] for e in errs[:3]))
        cur_mod = cur.get("module")
        on_cur = (mods.get(cur_mod) or {}).get("seconds", 0) if cur_mod else 0
        if errs or (cur_mod and cur_mod.isdigit() and on_cur >= MODULE_MIN_STUCK and cur_mod not in (r.get("done") or [])):
            stuck.append(f"{r.get('username')}: " + (f"{len(errs)} error(s)" if errs else f"{_dur(on_cur)} on {cur.get('label')} without finishing it"))
    if not active:
        L.append("Nobody opened the guide in the last 24 hours.")
    L += ["", "Looks stuck:" if stuck else "Nobody looks stuck."] + [f"- {s}" for s in stuck]
    try:
        days = [dt.datetime.fromtimestamp(now - i * day, dt.timezone.utc).strftime("%Y%m%d") for i in (0, 1)]
        keys = [k for d in days for k in HD.s3_list(f"{HD.PREFIX}{d}/")]
        L += ["", f"Help desk conversations started in the last two days: {len(keys)}."]
    except Exception as e:
        L += ["", f"(help desk count unavailable: {str(e)[:80]})"]
    if quiet:
        L += ["", "Quiet for 7+ days: " + ", ".join(str(r.get("username")) for r in quiet) + "."]
    if never:
        L += ["", "No guide page views recorded yet (seat older than a day): " + ", ".join(str(r.get("username")) for r in never) + "."]
    L += ["", f"Admin page: {ADMIN_URL}"]
    subject = f"Workshop digest: {len(active)} active, {len(stuck)} stuck ({stamp})"
    return subject, "\n".join(L)


def send(subject, text):
    if not HD.SMTP_URL:
        HD.log("digest: no SMTP_URL, not sent"); return False
    u = urllib.parse.urlparse(HD.SMTP_URL)
    msg = EmailMessage(); msg["From"], msg["To"], msg["Subject"] = HD.MAIL_FROM, HD.MAIL_TO, subject; msg.set_content(text)
    try:
        with smtplib.SMTP(u.hostname, u.port or 587, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(urllib.parse.unquote(u.username or ""), urllib.parse.unquote(u.password or ""))
            s.send_message(msg)
        HD.log(f"digest sent to {HD.MAIL_TO}: {subject}"); return True
    except Exception as e:
        HD.log(f"digest failed: {e}"); return False


def _loop(rows_fn, k8s, hub_ns):
    time.sleep(90)
    while True:
        try:
            now = dt.datetime.now(dt.timezone.utc)
            marker = (P.load(k8s, hub_ns).get("_digest") or {})
            if now.hour >= HOUR and marker.get("last_date") != now.strftime("%Y-%m-%d"):
                subject, text = compose(rows_fn())
                if send(subject, text):
                    k8s("PATCH", f"/api/v1/namespaces/{hub_ns}/configmaps/{P.CM}", {"data": {"_digest": json.dumps({"last_date": now.strftime("%Y-%m-%d"), "subject": subject})}})
        except Exception as e:
            HD.log(f"digest loop: {e}")
        time.sleep(300)


def start(rows_fn, k8s, hub_ns):
    threading.Thread(target=_loop, args=(rows_fn, k8s, hub_ns), daemon=True).start()
