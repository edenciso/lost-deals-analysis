#!/usr/bin/env python3
"""
FDE Demo Runner
================
Usage:
    python demo_runner.py --api-url https://xxx.execute-api.us-east-1.amazonaws.com/dev --token YOUR_TOKEN
    python demo_runner.py --api-url http://127.0.0.1:3000  (SAM local)
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error


def load_sample_data(path):
    with open(path) as f:
        return json.load(f)


def api_call(base_url, method, path, body=None, token=""):
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()}
    except Exception as e:
        return {"error": 0, "message": str(e)}


def poll_status(api_url, session_id, token, timeout=600, interval=10):
    """Poll /status/{session_id} until analysis completes or times out."""
    start = time.time()
    last_count = -1
    while time.time() - start < timeout:
        res = api_call(api_url, "GET", f"/status/{session_id}", token=token)
        status = res.get("status", "unknown")
        pipeline = res.get("pipeline", {})
        analyzed = pipeline.get("analyses_completed", 0)
        ingested = pipeline.get("deals_ingested", 0)

        if analyzed != last_count:
            last_count = analyzed
            elapsed = int(time.time() - start)
            print(f"    [{elapsed:>3}s] Status: {status} | Analyzed: {analyzed}/{ingested}")

        if status == "analyzed":
            return res
        if status not in ("analyzing", "ready_for_analysis"):
            # Could be error or already completed
            if analyzed > 0:
                return res

        time.sleep(interval)

    print("    TIMEOUT waiting for analysis to complete.")
    return api_call(api_url, "GET", f"/status/{session_id}", token=token)


def run_demo(api_url, token="", data_file="sample-data/demo_10_deals.json"):
    sep = "=" * 70
    print(f"\n{sep}")
    print("  LOSTBOT 90-DAY PILOT — FDE LIVE DEMO")
    print(sep)

    data = load_sample_data(data_file)
    lost = [d for d in data["deals"] if d["stage"] == "closedlost"]
    won = [d for d in data["deals"] if d["stage"] == "closedwon"]
    total_lost_arr = sum(d["arr_value"] for d in lost)

    print(f"\n  Company:   {data['company_name']}")
    print(f"  Deals:     {len(data['deals'])} ({len(lost)} lost, {len(won)} won)")
    print(f"  Lost ARR:  ${total_lost_arr:,.0f}")

    # ── Step 1: Ingest ──
    print(f"\n{'─'*70}")
    print("  STEP 1 / 4 — Ingesting deals")
    t0 = time.time()
    res = api_call(api_url, "POST", "/deals/ingest/batch", data, token)
    if "error" in res and isinstance(res.get("error"), int):
        print(f"  ERROR {res['error']}: {res.get('message','')}")
        sys.exit(1)
    sid = res.get("session_id", "?")
    print(f"  Session:   {sid}")
    print(f"  Ingested:  {res.get('deals_ingested',0)} deals  ({time.time()-t0:.1f}s)")

    # ── Step 2: Analyze (async) ──
    print(f"\n{'─'*70}")
    print("  STEP 2 / 4 — Multi-agent analysis (async, polling for progress)")
    t0 = time.time()
    res = api_call(api_url, "POST", "/analyze/batch", {"session_id": sid}, token)
    if "error" in res and isinstance(res.get("error"), int):
        print(f"  ERROR: {res}")
        sys.exit(1)
    print(f"  Queued: {res.get('deals_queued',0)} deals for analysis")
    print(f"  Polling for completion...")

    status_res = poll_status(api_url, sid, token, timeout=600, interval=8)
    elapsed = time.time() - t0
    pipeline = status_res.get("pipeline", {})
    print(f"  Done! {pipeline.get('analyses_completed',0)} analyses in {elapsed:.0f}s")

    # ── Step 3: Generate Report ──
    print(f"\n{'─'*70}")
    print("  STEP 3 / 4 — Generating executive report (GTM bugs + playbook)")
    t0 = time.time()
    res = api_call(api_url, "POST", "/reports/generate", {"session_id": sid}, token)
    if "error" in res and isinstance(res.get("error"), int):
        print(f"  ERROR: {res}")
        sys.exit(1)

    rid = res.get("report_id", "?")
    print(f"  Report ID:  {rid}")
    print(f"  Polling for completion...")

    # Poll the report endpoint until status=completed
    for attempt in range(90):
        time.sleep(8)
        rpt = api_call(api_url, "GET", f"/reports/{rid}?session_id={sid}", token=token)
        status = rpt.get("status") or rpt.get("report_data", {}).get("status", "")
        elapsed = int(time.time() - t0)
        print(f"    [{elapsed:>3}s] Report status: {status}")
        if status == "completed":
            res = rpt
            break
    else:
        print("    TIMEOUT waiting for report.")
        res = rpt

    print(f"  Report done! ({time.time()-t0:.0f}s)")
    rid = res.get("report_id", rid)

    # Extract executive report from response
    er = res.get("executive_report") or res.get("report_data", {}).get("executive_report", {})

    # ── Step 4: Display Results ──
    print(f"\n{sep}")
    print("  EXECUTIVE SUMMARY")
    print(sep)
    print(f"\n{er.get('executive_summary', '(see full report)')}")

    km = er.get("key_metrics", {})
    if km:
        print(f"\n  {'METRIC':<30} {'VALUE':>15}")
        print(f"  {'─'*46}")
        for label, key in [
            ("Total Deals Analyzed", "total_deals_analyzed"),
            ("Closed Lost", "closed_lost_count"),
            ("Total ARR Lost", "total_arr_lost"),
            ("Preventable ARR", "preventable_arr"),
            ("Preventable %", "preventable_percentage"),
        ]:
            val = km.get(key, 0)
            if "arr" in key.lower() or "lost" in key.lower():
                print(f"  {label:<30} ${val:>14,.0f}")
            elif "percentage" in key:
                print(f"  {label:<30} {val:>14.0f}%")
            else:
                print(f"  {label:<30} {val:>15}")

    bugs = er.get("gtm_bugs_summary", [])
    if bugs:
        print(f"\n  TOP GTM BUGS")
        print(f"  {'─'*46}")
        for b in bugs[:5]:
            title = b.get("title", "?")[:35]
            impact = b.get("revenue_impact", 0)
            print(f"  * {title:<35} ${impact:>8,.0f}")

    wbs = er.get("win_back_opportunities", [])
    if wbs:
        print(f"\n  WIN-BACK OPPORTUNITIES")
        print(f"  {'─'*46}")
        for w in wbs[:5]:
            co = w.get("company", "?")[:25]
            val = w.get("arr_value", 0)
            prob = w.get("probability", "?")
            print(f"  * {co:<25} ${val:>8,.0f}  [{prob}]")

    ap = er.get("action_plan", {})
    if ap:
        print(f"\n  30-DAY ACTION PLAN")
        print(f"  {'─'*46}")
        for a in ap.get("next_30_days", [])[:5]:
            print(f"  -> {a}")

    print(f"\n{sep}")
    durl = res.get("download_url")
    if durl:
        print(f"  Full report download: {durl[:80]}...")
    print(f"  Retrieve: GET /reports/{rid}?session_id={sid}")
    print(sep + "\n")

    # Save locally
    out = f"output_report_{sid}.json"
    with open(out, "w") as f:
        json.dump(res, f, indent=2, default=str)
    print(f"  Saved locally: {out}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Lostbot FDE Demo Runner")
    p.add_argument("--api-url", required=True, help="API Gateway base URL")
    p.add_argument("--token", default="", help="Cognito JWT token")
    p.add_argument("--data", default="sample-data/demo_10_deals.json", help="Input JSON")
    args = p.parse_args()
    run_demo(args.api_url, args.token, args.data)
