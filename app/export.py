from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_TITLE = "Compliance Zoeker — Screeningsrapport"
_DISCLAIMER = (
    "Disclaimer: een match-score is een risico-indicatie, geen veroordeling. "
    "Een 'Politically Exposed Person'-vermelding is een risicocategorie, geen beschuldiging. "
    "De gegevens komen uit de EU-sanctielijst (FSF) en OpenSanctions (CC BY-NC 4.0). "
    "Dit rapport vormt geen juridisch advies."
)


def _escape(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _query_lines(query: dict) -> list[str]:
    lines = [f"Naam: {_escape(query.get('name', ''))}"]
    labels = [("birth_year", "Geboortejaar"), ("nationality", "Nationaliteit"), ("birth_place", "Geboorteplaats"), ("entity_type", "Type")]
    for key, label in labels:
        value = query.get(key)
        if value:
            lines.append(f"{label}: {_escape(value)}")
    return lines


def _result_paragraphs(result: dict, styles) -> list:
    source = result.get("source", "?")
    source_label = {"eu": "EU sanctielijst", "pep": "PEP", "opensanctions": "OpenSanctions"}.get(source, source)
    parts = [Paragraph(f"<b>{_escape(result['entity'].get('name', ''))}</b> — score {result.get('score', 0)}/100 ({_escape(source_label)})", styles["h3"])]
    details = result.get(source, {}).get("details") or result.get("pep", {}).get("details") or []
    for d in details:
        parts.append(Paragraph(f"&bull; {_escape(d.get('label', ''))}", styles["body"]))
    entity = result.get("entity", {})
    if result.get("eu") is not None:
        parts.append(Paragraph(f"EU-referentie: {_escape(entity.get('eu_reference_number', ''))}", styles["body"]))
    if result.get("pep") is not None:
        for ds in result["pep"].get("datasets", []):
            parts.append(Paragraph(f"Bron: {_escape(ds.get('title', ''))} ({_escape(ds.get('country', '').upper())}) — {_escape(ds.get('url', ''))}", styles["body"]))
        parts.append(Paragraph(f"Details: {_escape(result['pep'].get('url', ''))}", styles["body"]))
    parts.append(Spacer(1, 4))
    return parts


def render_search_pdf(payload: dict) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=_TITLE)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    styles.add(body)
    story = [
        Paragraph(f"<b>{_TITLE}</b>", styles["Title"]),
        Paragraph(f"Versie: {_escape(payload.get('version', 'dev'))}", body),
        Spacer(1, 4 * mm),
    ]
    story.append(Paragraph("<b>Zoekopdracht</b>", styles["h2"]))
    for line in _query_lines(payload["query"]):
        story.append(Paragraph(line, body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("<b>Uitgevoerd</b>", styles["h2"]))
    story.append(Paragraph(f"Op: {_escape(payload.get('generated_at', ''))}", body))
    if payload.get("author"):
        story.append(Paragraph(f"Door: {_escape(payload['author'])}", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("<b>Dataversies</b>", styles["h2"]))
    meta = payload.get("meta", {}) or {}
    story.append(Paragraph(f"EU-lijst generatie: {_escape(meta.get('generation_date', 'onbekend'))}", body))
    pep_meta = payload.get("pep_meta", {}) or {}
    story.append(Paragraph(f"PEP-update: {_escape(pep_meta.get('updated_at', 'onbekend'))}", body))
    story.append(Spacer(1, 4 * mm))
    results = payload.get("results", [])
    capped = len(results) >= payload.get("max_results", 20)
    story.append(Paragraph("<b>Resultaten</b>", styles["h2"]))
    story.append(Paragraph(f"Getoond: {len(results)} | drempel: {payload.get('threshold', 90)}%{ ' | LET OP: cap bereikt, mogelijk meer resultaten' if capped else ''}", body))
    story.append(Spacer(1, 2 * mm))
    if not results:
        story.append(Paragraph("Geen overeenkomsten gevonden.", body))
    for result in results:
        story.extend(_result_paragraphs(result, styles))
    for warning in payload.get("warnings", []):
        story.append(Paragraph(f"<b>Waarschuwing:</b> {_escape(warning)}", body))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"<i>{_DISCLAIMER}</i>", body))
    doc.build(story)
    return buffer.getvalue()
