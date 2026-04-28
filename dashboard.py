"""
dashboard.py — Flask web dashboard for AVVA Properties. Runs on port 5000.
All templates inlined as Python strings. No external CSS frameworks.
"""

import json
import logging
import os
import queue
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("dashboard")

DB_PATH = "leads.db"
app = Flask(__name__)

# Auto-initialize DB so dashboard works even if db_init.py was never run
import db_init as _db_init
_db_init.init_db(DB_PATH)

# ---------------------------------------------------------------------------
# Base layout
# ---------------------------------------------------------------------------

BASE_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { display: flex; min-height: 100vh; font-family: 'Segoe UI', sans-serif; background: #f0f0f5; }
  #sidebar {
    width: 220px; min-height: 100vh; background: #1a1a2e; color: #eee;
    display: flex; flex-direction: column; padding: 24px 0;
    position: fixed; top: 0; left: 0; z-index: 100;
  }
  #sidebar .logo { padding: 0 20px 24px; font-size: 1.1rem; font-weight: 700;
    color: #7c5cbf; border-bottom: 1px solid #2e2e4e; }
  #sidebar a {
    display: block; padding: 12px 20px; color: #ccc; text-decoration: none;
    font-size: 0.9rem; transition: background 0.2s;
  }
  #sidebar a:hover, #sidebar a.active { background: #2e2e4e; color: #fff; }
  #main { margin-left: 220px; padding: 32px; width: calc(100% - 220px); }
  h1 { font-size: 1.5rem; margin-bottom: 24px; color: #1a1a2e; }
  h2 { font-size: 1.1rem; margin-bottom: 16px; color: #333; }
  .card { background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.07); }
  .metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 28px; }
  .metric { background: #fff; border-radius: 10px; padding: 20px; text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.07); }
  .metric .number { font-size: 2.2rem; font-weight: 700; color: #7c5cbf; }
  .metric .label { font-size: 0.8rem; color: #888; margin-top: 4px; }
  .badge {
    display: inline-block; padding: 2px 10px; border-radius: 20px;
    font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  }
  .badge-new { background: #e8f4ff; color: #0066cc; }
  .badge-skip_traced { background: #fff3e0; color: #e65100; }
  .badge-contacted { background: #e8eaf6; color: #3949ab; }
  .badge-hot { background: #fce4ec; color: #c62828; }
  .badge-dead { background: #f5f5f5; color: #757575; }
  .badge-deal { background: #e8f5e9; color: #2e7d32; }
  .grade-A { background: #e8f5e9; color: #2e7d32; }
  .grade-B { background: #e3f2fd; color: #1565c0; }
  .grade-C { background: #fffde7; color: #f57f17; }
  .grade-D { background: #fce4ec; color: #c62828; }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  th { background: #f5f5fa; padding: 10px 12px; text-align: left; font-weight: 600;
    color: #555; border-bottom: 2px solid #e0e0e0; cursor: pointer; }
  td { padding: 9px 12px; border-bottom: 1px solid #f0f0f0; color: #333; }
  tr:hover { background: #fafafa; }
  .btn {
    padding: 7px 16px; border: none; border-radius: 6px; cursor: pointer;
    font-size: 0.82rem; font-weight: 600; transition: opacity 0.2s;
  }
  .btn:hover { opacity: 0.85; }
  .btn-green { background: #4caf50; color: #fff; }
  .btn-red { background: #ef5350; color: #fff; }
  .btn-purple { background: #7c5cbf; color: #fff; margin-right: 10px; }
  .activity-feed { margin-top: 8px; }
  .activity-item { padding: 10px 0; border-bottom: 1px solid #f0f0f0; font-size: 0.85rem; color: #555; }
  .lead-card { border: 1px solid #e0e0e0; border-radius: 10px; padding: 18px;
    margin-bottom: 16px; background: #fff; }
  .lead-card .header { display: flex; justify-content: space-between; align-items: flex-start; }
  .terminal {
    background: #1a1a2e; color: #00ff88; font-family: 'Courier New', monospace;
    font-size: 0.82rem; padding: 16px; border-radius: 8px;
    height: 360px; overflow-y: auto; white-space: pre-wrap; margin-top: 12px;
  }
  .run-btn-row { display: flex; gap: 12px; margin-bottom: 12px; }
  .detail-panel {
    background: #f9f9fd; border: 1px solid #e0e0e0; border-radius: 8px;
    padding: 14px; margin-top: 4px; font-size: 0.83rem; display: none;
  }
  .detail-panel.open { display: block; }
</style>
"""

SIDEBAR = """
<div id="sidebar">
  <div class="logo">AVVA Properties</div>
  <a href="/" class="{ov}">Overview</a>
  <a href="/hot" class="{hot}">Hot Leads</a>
  <a href="/leads" class="{all}">All Leads</a>
  <a href="/tools" class="{tools}">Run Tools</a>
</div>
"""


def _sidebar(active: str) -> str:
    keys = {"ov": "", "hot": "", "all": "", "tools": ""}
    keys[active] = "active"
    return SIDEBAR.format(**keys)


def _base(title: str, active: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — AVVA Properties</title>{BASE_STYLE}</head>
<body>
{_sidebar(active)}
<div id="main">{body}</div>
</body></html>"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _counts() -> dict:
    conn = _db()
    cur = conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")
    counts = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    return counts


def _recent_activity(limit: int = 20) -> list:
    conn = _db()
    rows = conn.execute(
        """SELECT ol.sent_at, ol.channel, ol.status, ol.sequence_day,
                  l.owner_name, l.property_address
           FROM outreach_log ol JOIN leads l ON l.id = ol.lead_id
           ORDER BY ol.sent_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def _hot_leads_data() -> list:
    conn = _db()
    rows = conn.execute(
        """SELECT l.id, l.owner_name, l.property_address, l.city, l.county, l.source,
                  da.arv, da.estimated_repair_cost, da.mao, da.suggested_offer,
                  da.deal_grade, da.summary
           FROM leads l
           LEFT JOIN deal_analysis da ON da.lead_id = l.id
           WHERE l.status = 'hot'
           ORDER BY CASE WHEN da.deal_grade IS NULL THEN 1 ELSE 0 END,
                    da.deal_grade, l.created_at DESC"""
    ).fetchall()
    conn.close()
    return rows


def _all_leads(sort_by: str = "created_at", order: str = "desc") -> list:
    allowed_cols = {"status", "county", "source", "created_at", "owner_name"}
    col = sort_by if sort_by in allowed_cols else "created_at"
    direction = "ASC" if order == "asc" else "DESC"
    conn = _db()
    rows = conn.execute(
        f"SELECT * FROM leads ORDER BY {col} {direction}"
    ).fetchall()
    conn.close()
    return rows


def _lead_detail(lead_id: int) -> dict:
    conn = _db()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    contact = conn.execute("SELECT * FROM contacts WHERE lead_id = ?", (lead_id,)).fetchone()
    outreach = conn.execute(
        "SELECT * FROM outreach_log WHERE lead_id = ? ORDER BY sent_at", (lead_id,)
    ).fetchall()
    analysis = conn.execute(
        "SELECT * FROM deal_analysis WHERE lead_id = ? ORDER BY analyzed_at DESC LIMIT 1",
        (lead_id,),
    ).fetchone()
    conn.close()
    return {"lead": lead, "contact": contact, "outreach": outreach, "analysis": analysis}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def overview():
    counts = _counts()
    activity = _recent_activity()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    conn = _db()
    hot_week = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE status = 'hot' AND created_at >= ?", (week_ago,)
    ).fetchone()[0]
    conn.close()

    metrics = f"""
    <div class="metric-grid">
      <div class="metric"><div class="number">{counts.get('new', 0) + sum(counts.get(s, 0) for s in ['skip_traced','contacted','hot','deal'])}</div><div class="label">Total Leads</div></div>
      <div class="metric"><div class="number">{counts.get('skip_traced', 0)}</div><div class="label">Skip Traced</div></div>
      <div class="metric"><div class="number">{counts.get('contacted', 0)}</div><div class="label">Contacted</div></div>
      <div class="metric"><div class="number">{hot_week}</div><div class="label">Hot This Week</div></div>
    </div>"""

    feed_items = "".join(
        f'<div class="activity-item">{r["sent_at"][:16]} — '
        f'<strong>{r["channel"].upper()}</strong> day {r["sequence_day"]} '
        f'({r["status"]}) to {r["owner_name"]} — {r["property_address"]}</div>'
        for r in activity
    )
    feed = f'<div class="card"><h2>Recent Activity</h2><div class="activity-feed">{feed_items or "<p style=color:#aaa>No outreach yet.</p>"}</div></div>'

    return _base("Overview", "ov", f"<h1>Overview</h1>{metrics}{feed}")


@app.route("/hot")
def hot_leads():
    rows = _hot_leads_data()
    cards = []
    for r in rows:
        grade = r["deal_grade"] or "?"
        cards.append(f"""
        <div class="lead-card">
          <div class="header">
            <div>
              <strong>{r['owner_name']}</strong><br>
              <span style="color:#666;font-size:.87rem">{r['property_address']}, {r['city']} FL &bull; {r['county'].title()}</span>
            </div>
            <span class="badge grade-{grade}">{grade}</span>
          </div>
          <div style="margin-top:12px;display:grid;grid-template-columns:repeat(3,1fr);gap:8px;font-size:.85rem">
            <div><label style="color:#888;font-size:.75rem">ARV</label><br>${r['arv']:,.0f if r['arv'] else 'TBD'}</div>
            <div><label style="color:#888;font-size:.75rem">MAO</label><br>${r['mao']:,.0f if r['mao'] else 'TBD'}</div>
            <div><label style="color:#888;font-size:.75rem">Suggested Offer</label><br>${r['suggested_offer']:,.0f if r['suggested_offer'] else 'TBD'}</div>
          </div>
          <p style="margin-top:10px;font-size:.84rem;color:#555">{r['summary'] or ''}</p>
          <div style="margin-top:12px;display:flex;gap:8px">
            <button class="btn btn-green" onclick="markLead({r['id']},'deal',this)">Mark Deal</button>
            <button class="btn btn-red" onclick="markLead({r['id']},'dead',this)">Mark Dead</button>
          </div>
        </div>""")

    script = """
    <script>
    function markLead(id, status, btn) {
      fetch('/api/lead/'+id+'/status', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({status:status})
      }).then(r=>r.json()).then(d=>{
        if(d.ok){ btn.closest('.lead-card').style.opacity='0.4';
          btn.closest('.lead-card').querySelector('strong').textContent += ' ['+status.toUpperCase()+']'; }
      });
    }
    </script>"""

    body = f"<h1>Hot Leads</h1>{''.join(cards) or '<div class=card><p style=color:#aaa>No hot leads yet.</p></div>'}{script}"
    return _base("Hot Leads", "hot", body)


@app.route("/leads")
def all_leads():
    sort_by = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc")
    rows = _all_leads(sort_by, order)

    def sort_link(col: str) -> str:
        new_order = "asc" if sort_by == col and order == "desc" else "desc"
        arrow = " ▼" if sort_by == col and order == "desc" else (" ▲" if sort_by == col else "")
        return f'<a href="/leads?sort={col}&order={new_order}" style="color:inherit;text-decoration:none">{col.replace("_"," ").title()}{arrow}</a>'

    table_rows = ""
    for r in rows:
        table_rows += f"""
        <tr onclick="toggleDetail(this, {r['id']})">
          <td>{r['id']}</td>
          <td>{r['owner_name']}</td>
          <td>{r['property_address']}</td>
          <td>{r['county'].title() if r['county'] else ''}</td>
          <td>{r['source']}</td>
          <td><span class="badge badge-{r['status']}">{r['status']}</span></td>
          <td>{r['created_at'][:10] if r['created_at'] else ''}</td>
        </tr>
        <tr><td colspan="7" style="padding:0"><div class="detail-panel" id="detail-{r['id']}">Loading...</div></td></tr>"""

    script = """
    <script>
    function toggleDetail(row, id) {
      const panel = document.getElementById('detail-'+id);
      if (panel.classList.contains('open')) { panel.classList.remove('open'); return; }
      panel.classList.add('open');
      if (panel.dataset.loaded) return;
      fetch('/api/lead/'+id+'/detail').then(r=>r.json()).then(d=>{
        panel.dataset.loaded = '1';
        let html = '<div style="padding:10px">';
        if(d.contact){
          html += '<strong>Contact:</strong> '+
            (d.contact.phone_1||'')+(d.contact.phone_2?' / '+d.contact.phone_2:'')+
            ' | '+(d.contact.email_1||'')+' | Mailing: '+(d.contact.mailing_address||'N/A')+'<br>';
        }
        if(d.outreach && d.outreach.length){
          html += '<strong>Outreach:</strong> ';
          d.outreach.forEach(o=>{ html += 'Day '+o.sequence_day+' '+o.channel+' ('+o.status+') '; });
          html += '<br>';
        }
        if(d.analysis){
          html += '<strong>Deal:</strong> Grade '+d.analysis.deal_grade+
            ' | ARV $'+Number(d.analysis.arv||0).toLocaleString()+
            ' | Offer $'+Number(d.analysis.suggested_offer||0).toLocaleString()+
            '<br><em>'+d.analysis.summary+'</em>';
        }
        html += '</div>';
        panel.innerHTML = html;
      });
    }
    </script>"""

    table = f"""
    <div class="card" style="overflow-x:auto">
    <table>
      <thead><tr>
        <th>#</th><th>{sort_link('owner_name')}</th><th>Address</th>
        <th>{sort_link('county')}</th><th>{sort_link('source')}</th>
        <th>{sort_link('status')}</th><th>{sort_link('created_at')}</th>
      </tr></thead>
      <tbody>{table_rows}</tbody>
    </table></div>"""

    return _base("All Leads", "all", f"<h1>All Leads ({len(rows)})</h1>{table}{script}")


@app.route("/tools")
def run_tools():
    body = """
    <h1>Run Tools</h1>
    <div class="card">
      <div class="run-btn-row">
        <button class="btn btn-purple" onclick="runTool('scraper')">Run Scraper</button>
        <button class="btn btn-purple" onclick="runTool('skiptracer')">Run Skip Tracer</button>
        <button class="btn btn-purple" onclick="runTool('outreach')">Run Outreach</button>
      </div>
      <div id="terminal-label" style="color:#888;font-size:.82rem;margin-bottom:4px">Select a tool to run</div>
      <div class="terminal" id="terminal"></div>
    </div>
    <script>
    let es = null;
    function runTool(name) {
      if(es){ es.close(); }
      const term = document.getElementById('terminal');
      term.textContent = '';
      document.getElementById('terminal-label').textContent = 'Running '+name+'...';
      es = new EventSource('/stream/'+name);
      es.onmessage = function(e) {
        term.textContent += e.data + '\\n';
        term.scrollTop = term.scrollHeight;
      };
      es.onerror = function() {
        es.close();
        document.getElementById('terminal-label').textContent = name+' finished.';
      };
    }
    </script>"""
    return _base("Run Tools", "tools", body)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/lead/<int:lead_id>/status", methods=["POST"])
def api_set_status(lead_id: int):
    data = request.get_json()
    status = data.get("status")
    if status not in {"deal", "dead", "hot", "contacted"}:
        return jsonify({"ok": False, "error": "invalid status"}), 400
    conn = _db()
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lead/<int:lead_id>/detail")
def api_lead_detail(lead_id: int):
    d = _lead_detail(lead_id)
    return jsonify({
        "contact": dict(d["contact"]) if d["contact"] else None,
        "outreach": [dict(o) for o in d["outreach"]],
        "analysis": dict(d["analysis"]) if d["analysis"] else None,
    })


# ---------------------------------------------------------------------------
# SSE streaming endpoint
# ---------------------------------------------------------------------------

@app.route("/stream/<module>")
def stream_tool(module: str):
    scripts = {"scraper": "scraper.py", "skiptracer": "skiptracer.py", "outreach": "outreach.py"}
    if module not in scripts:
        return "Invalid module", 400

    def generate():
        proc = subprocess.Popen(
            ["python", "-u", scripts[module]],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:
            yield f"data: {line.rstrip()}\n\n"
        proc.wait()
        yield "data: [Process exited with code {}]\n\n".format(proc.returncode)

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
