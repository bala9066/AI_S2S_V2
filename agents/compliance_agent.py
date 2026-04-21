"""
Phase 3: Compliance Validation Agent

Checks components against RoHS, REACH, FCC, CE, and other standards.
Uses rules engine + Claude for edge case classification.
"""

import logging
from pathlib import Path

from agents.base_agent import BaseAgent
# from agents.sbom_generator import generate_sbom  # SBOM removed from pipeline
from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a regulatory compliance expert for electronics hardware.

Given a list of components and their specifications, validate compliance against:
- RoHS (Restriction of Hazardous Substances) - EU Directive 2011/65/EU
- REACH (Registration, Evaluation, Authorization, Restriction of Chemicals)
- FCC Part 15 (EMC requirements for US)
- CE Marking (European conformity)
- Medical (IEC 60601) - if applicable
- Automotive (ISO 26262) - if applicable
- Military (MIL-STD) - if applicable

For each component, provide:
1. PASS / FAIL / REVIEW status for each applicable standard
2. Specific concerns or restrictions
3. Recommended alternatives if a component fails

Output as a structured markdown compliance report with tables.
Include a summary compliance matrix at the top.

IMPORTANT: Do NOT use TBD, TBA, or TBC placeholders. Derive specific values from the
provided component data, use engineering judgment, or state a justified assumption inline.
Every field must have a concrete value.
"""


class ComplianceAgent(BaseAgent):
    """Phase 3: Compliance validation against regulatory standards."""

    def __init__(self):
        super().__init__(
            phase_number="P3",
            phase_name="Compliance Validation",
            model=settings.fast_model,
            max_tokens=16384,
        )

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # Load component recommendations from Phase 1
        comp_file = output_dir / "component_recommendations.md"
        components = ""
        if comp_file.exists():
            components = comp_file.read_text(encoding="utf-8")

        # Load requirements for compliance context
        req_file = output_dir / "requirements.md"
        requirements = ""
        if req_file.exists():
            requirements = req_file.read_text(encoding="utf-8")

        if not components:
            return {
                "response": "No component data found. Complete Phase 1 first.",
                "phase_complete": False,
                "outputs": {},
            }

        user_message = f"""Validate compliance for the following hardware design:

**Project:** {project_name}

### Requirements (for compliance context):
{requirements[:3000]}

### Components to Validate:
{components}

Generate a complete compliance report with:
1. Summary compliance matrix (table)
2. Per-component detailed analysis
3. Risk items requiring human review
4. Recommendations for any non-compliant components
"""

        response = await self.call_llm(
            messages=[{"role": "user", "content": user_message}],
            system=self.get_system_prompt(project_context),
        )

        import re as _re
        report_content = response.get("content", "")
        report_content = _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', report_content, flags=_re.IGNORECASE)

        # Save compliance report
        report_file = output_dir / "compliance_report.md"
        report_file.write_text(report_content, encoding="utf-8")

        self.log(f"Compliance report generated: {len(report_content)} chars")

        outputs = {report_file.name: report_content}

        return {
            "response": "Compliance validation complete.",
            "phase_complete": True,
            "outputs": outputs,
        }
