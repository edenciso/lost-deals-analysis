"""
Lostbot Multi-Agent Orchestrator
=================================
Coordinates the sequential execution of specialized agents to produce
the full analysis pipeline: Ingest → Analyze → Synthesize → Report.
"""

import json
import logging
from typing import Any

from agents.agent_definitions import get_agent

logger = logging.getLogger(__name__)

from common import invoke_claude_json, invoke_claude, utc_now_iso


class LostbotOrchestrator:
    """Orchestrates multi-agent analysis pipeline."""

    def __init__(self):
        self.results = {}
        self.execution_log = []

    def _log_step(self, agent_name: str, status: str, details: str = ""):
        entry = {
            "agent": agent_name,
            "status": status,
            "timestamp": utc_now_iso(),
            "details": details,
        }
        self.execution_log.append(entry)
        logger.info(f"[{agent_name}] {status}: {details}")

    # ------------------------------------------
    # Step 1: Analyze individual deal losses
    # ------------------------------------------
    def analyze_single_deal(self, deal: dict) -> dict:
        """Run root cause + revenue impact + product feedback on one deal."""
        deal_id = deal.get("deal_id", "unknown")
        self._log_step("root_cause_analyst", "started", f"deal={deal_id}")

        agent = get_agent("root_cause_analyst")
        prompt = f"""Analyze this closed-lost B2B deal:

Deal Information:
- Deal ID: {deal.get('deal_id')}
- Company: {deal.get('company')}
- ARR Value: ${deal.get('arr_value', 0):,.0f}
- Days in Cycle: {deal.get('days_in_cycle', 'N/A')}
- AE Name: {deal.get('ae_name', 'N/A')}
- AE Stated Loss Reason: {deal.get('ae_stated_reason', 'N/A')}
- Competitor: {deal.get('competitor', 'N/A')}
- Close Date: {deal.get('close_date', 'N/A')}
- Product Evaluated: {deal.get('product_evaluated', 'N/A')}
- Discount Offered: {deal.get('discount_offered', 'N/A')}%

Contacts Engaged:
{json.dumps(deal.get('contacts', []), indent=2)}

Call Transcripts/Summaries:
{json.dumps(deal.get('call_transcripts', []), indent=2)}

Email Threads:
{json.dumps(deal.get('email_threads', []), indent=2)}

Additional Notes:
{deal.get('notes', 'None')}

Analyze this deal and return the structured JSON analysis."""

        try:
            root_cause = invoke_claude_json(prompt=prompt, system=agent["system_prompt"])
            self._log_step("root_cause_analyst", "completed", f"deal={deal_id}")
        except Exception as e:
            self._log_step("root_cause_analyst", "error", str(e))
            root_cause = {"data": {"error": str(e), "deal_id": deal_id}}

        # Revenue Impact
        self._log_step("revenue_impact_assessor", "started", f"deal={deal_id}")
        rev_agent = get_agent("revenue_impact_assessor")
        rev_prompt = f"""Assess the revenue impact of this lost deal:

Deal: {deal.get('company')} - ${deal.get('arr_value', 0):,.0f}
Days in cycle: {deal.get('days_in_cycle', 'N/A')}
Root cause analysis: {json.dumps(root_cause.get('data', {}), indent=2)}

Calculate all revenue impact metrics and win-back potential."""

        try:
            revenue = invoke_claude_json(prompt=rev_prompt, system=rev_agent["system_prompt"])
            self._log_step("revenue_impact_assessor", "completed", f"deal={deal_id}")
        except Exception as e:
            self._log_step("revenue_impact_assessor", "error", str(e))
            revenue = {"data": {"error": str(e), "deal_id": deal_id}}

        # Product Feedback
        self._log_step("product_feedback_extractor", "started", f"deal={deal_id}")
        prod_agent = get_agent("product_feedback_extractor")
        prod_prompt = f"""Extract product feedback from this lost deal:

Deal: {deal.get('company')} - ${deal.get('arr_value', 0):,.0f}
AE stated reason: {deal.get('ae_stated_reason', 'N/A')}
Root cause: {json.dumps(root_cause.get('data', {}), indent=2)}

Call Transcripts:
{json.dumps(deal.get('call_transcripts', []), indent=2)}

Email Threads:
{json.dumps(deal.get('email_threads', []), indent=2)}

Extract all product-related feedback."""

        try:
            product = invoke_claude_json(prompt=prod_prompt, system=prod_agent["system_prompt"])
            self._log_step("product_feedback_extractor", "completed", f"deal={deal_id}")
        except Exception as e:
            self._log_step("product_feedback_extractor", "error", str(e))
            product = {"data": {"error": str(e), "deal_id": deal_id}}

        return {
            "deal_id": deal_id,
            "company": deal.get("company"),
            "arr_value": deal.get("arr_value", 0),
            "root_cause_analysis": root_cause.get("data", {}),
            "revenue_impact": revenue.get("data", {}),
            "product_feedback": product.get("data", {}),
            "agent_thinking": {
                "root_cause": root_cause.get("thinking"),
                "revenue": revenue.get("thinking"),
                "product": product.get("thinking"),
            },
        }

    # ------------------------------------------
    # Step 2: Identify GTM Bugs across all deals
    # ------------------------------------------
    def identify_gtm_bugs(self, all_analyses: list) -> dict:
        """Find systematic GTM bugs across all analyzed deals."""
        self._log_step("gtm_bug_detector", "started", f"deals={len(all_analyses)}")

        agent = get_agent("gtm_bug_detector")

        # Compress analyses for context
        compressed = []
        for a in all_analyses:
            compressed.append({
                "deal_id": a["deal_id"],
                "company": a["company"],
                "arr_value": a["arr_value"],
                "root_cause": a.get("root_cause_analysis", {}).get("true_root_cause", "unknown"),
                "root_cause_category": a.get("root_cause_analysis", {}).get("root_cause_category", "unknown"),
                "preventable": a.get("root_cause_analysis", {}).get("preventable", False),
                "what_should_have_been_done": a.get("root_cause_analysis", {}).get("what_should_have_been_done", []),
            })

        prompt = f"""Analyze these {len(compressed)} deal loss analyses and identify systematic GTM bugs:

{json.dumps(compressed, indent=2)}

Find PATTERNS that appear in 2+ deals. Prioritize by revenue impact."""

        try:
            result = invoke_claude_json(prompt=prompt, system=agent["system_prompt"], max_tokens=6000)
            self._log_step("gtm_bug_detector", "completed", f"bugs_found={len(result.get('data', {}).get('bugs', []))}")
            return result.get("data", {})
        except Exception as e:
            self._log_step("gtm_bug_detector", "error", str(e))
            return {"error": str(e), "bugs": []}

    # ------------------------------------------
    # Step 3: Generate Winning Playbook
    # ------------------------------------------
    def generate_playbook(self, gtm_bugs: dict, all_analyses: list) -> dict:
        """Generate battle cards and winning playbook for identified bugs."""
        self._log_step("playbook_generator", "started", f"bugs={len(gtm_bugs.get('bugs', []))}")

        agent = get_agent("playbook_generator")

        # Only send bug data, not full analyses — keeps prompt small
        prompt = f"""Generate battle cards and a winning playbook for these GTM bugs:

GTM Bugs Identified:
{json.dumps(gtm_bugs, indent=2)}

Total deals analyzed: {len(all_analyses)}.

Create specific, actionable battle cards for each bug and an overall winning playbook."""

        try:
            result = invoke_claude_json(prompt=prompt, system=agent["system_prompt"], max_tokens=8000)
            self._log_step("playbook_generator", "completed")
            return result.get("data", {})
        except Exception as e:
            self._log_step("playbook_generator", "error", str(e))
            return {"error": str(e), "battle_cards": [], "winning_playbook": {}}

    # ------------------------------------------
    # Step 4: Synthesize Executive Report
    # ------------------------------------------
    def synthesize_report(
        self,
        company_name: str,
        all_analyses: list,
        gtm_bugs: dict,
        playbook: dict,
    ) -> dict:
        """Generate the final executive report."""
        self._log_step("report_synthesizer", "started", f"company={company_name}")

        agent = get_agent("report_synthesizer")

        # Compress analyses to essential fields only
        compressed_analyses = []
        for a in all_analyses:
            rc = a.get("root_cause_analysis", {})
            ri = a.get("revenue_impact", {})
            compressed_analyses.append({
                "deal_id": a.get("deal_id"),
                "company": a.get("company"),
                "arr_value": a.get("arr_value", 0),
                "true_root_cause": rc.get("true_root_cause", rc.get("raw_text", "unknown")[:200] if isinstance(rc.get("raw_text"), str) else "unknown"),
                "root_cause_category": rc.get("root_cause_category", "unknown"),
                "preventable": rc.get("preventable", False),
                "ae_stated_reason": rc.get("ae_stated_reason", ""),
                "ae_reason_aligned": rc.get("ae_reason_aligned", None),
                "what_should_have_been_done": rc.get("what_should_have_been_done", [])[:3],
                "win_back_probability": ri.get("win_back_probability", "unknown"),
                "win_back_value": ri.get("win_back_estimated_value", 0),
                "win_back_action": ri.get("win_back_recommended_action", ""),
            })

        # Compress playbook to just battle card titles
        playbook_summary = {
            "battle_card_count": len(playbook.get("battle_cards", [])),
            "battle_card_titles": [bc.get("bug_title", "") for bc in playbook.get("battle_cards", [])],
        }

        prompt = f"""Synthesize the complete Lostbot analysis into an executive report for {company_name}.

DEAL ANALYSES ({len(compressed_analyses)} deals):
{json.dumps(compressed_analyses, indent=2)}

GTM BUGS ({len(gtm_bugs.get('bugs', []))} identified):
{json.dumps(gtm_bugs, indent=2)}

PLAYBOOK SUMMARY:
{json.dumps(playbook_summary, indent=2)}

Generate a comprehensive, board-ready executive report."""

        try:
            result = invoke_claude_json(prompt=prompt, system=agent["system_prompt"], max_tokens=8000)
            report = result.get("data", {})
            report["generated_at"] = utc_now_iso()
            report["company"] = company_name
            report["execution_log"] = self.execution_log
            self._log_step("report_synthesizer", "completed")
            return report
        except Exception as e:
            self._log_step("report_synthesizer", "error", str(e))
            return {"error": str(e)}

    # ------------------------------------------
    # Full Pipeline: Run everything
    # ------------------------------------------
    def run_full_pipeline(self, session_data: dict) -> dict:
        """Execute the complete multi-agent pipeline."""
        company_name = session_data.get("company_name", "Unknown Company")
        deals = session_data.get("deals", [])

        self._log_step("orchestrator", "started", f"company={company_name}, deals={len(deals)}")

        # Step 1: Analyze each deal
        all_analyses = []
        for deal in deals:
            analysis = self.analyze_single_deal(deal)
            all_analyses.append(analysis)

        # Step 2: Identify GTM bugs
        gtm_bugs = self.identify_gtm_bugs(all_analyses)

        # Step 3: Generate playbook
        playbook = self.generate_playbook(gtm_bugs, all_analyses)

        # Step 4: Synthesize report
        report = self.synthesize_report(company_name, all_analyses, gtm_bugs, playbook)

        self._log_step("orchestrator", "completed", f"report_generated")

        return {
            "session_id": session_data.get("session_id"),
            "company_name": company_name,
            "deal_analyses": all_analyses,
            "gtm_bugs": gtm_bugs,
            "playbook": playbook,
            "executive_report": report,
            "execution_log": self.execution_log,
        }
