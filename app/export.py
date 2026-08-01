import csv
from io import BytesIO, StringIO

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

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
        lines.append(f"{label}: {_escape(value) if value else 'NVT'}")
    return lines


def _result_paragraphs(result: dict, styles) -> list:
    source = result.get("source", "?")
    source_label = {"eu": "EU sanctielijst", "pep": "PEP", "opensanctions": "OpenSanctions"}.get(source, source)
    parts = [Paragraph(f"<b>{_escape(result['entity'].get('name', ''))}</b> — score {result.get('score', 0)}/100 ({_escape(source_label)})", styles["h3"])]
    details = (result.get(source) or {}).get("details") or (result.get("pep") or {}).get("details") or []
    for d in details:
        parts.append(Paragraph(f"&bull; {_escape(d.get('label', ''))}", styles["body"]))
    entity = result.get("entity", {})
    if result.get("eu") is not None:
        raw = entity.get("raw") or entity
        parts.append(Paragraph(f"EU-referentie: {_escape(entity.get('eu_reference_number', ''))}", styles["body"]))
        un_id = raw.get("united_nations_id")
        if un_id:
            parts.append(Paragraph(f"VN-id: {_escape(un_id)}", styles["body"]))
        raw_aliases = raw.get("aliases", [])
        aliases = [a if isinstance(a, str) else a.get("whole_name", "") for a in raw_aliases]
        aliases = [a for a in aliases if a]
        if aliases:
            parts.append(Paragraph(f"Aliassen: {_escape(', '.join(aliases[:5]))}", styles["body"]))
        for birth in raw.get("birthdates", []):
            if not isinstance(birth, dict):
                continue
            when = birth.get("date") or birth.get("year")
            where = birth.get("place") or birth.get("city")
            value = " ".join(str(part) for part in (when, where) if part)
            if value:
                parts.append(Paragraph(f"Geboortedata/-plaats: {_escape(value)}", styles["body"]))
        for country in raw.get("citizenships", []):
            if not isinstance(country, dict):
                continue
            nationality = country.get("description") or country.get("iso2")
            if nationality:
                parts.append(Paragraph(f"Nationaliteit: {_escape(nationality)}", styles["body"]))
        function = entity.get("function") or next((a.get("function", "") for a in raw_aliases if isinstance(a, dict) and a.get("function")), "")
        if function:
            parts.append(Paragraph(f"Functie: {_escape(function)}", styles["body"]))
        for reg in raw.get("regulations", []):
            if not isinstance(reg, dict):
                continue
            title = reg.get("number_title") or reg.get("programme")
            url = reg.get("publication_url")
            value = " — ".join(filter(None, [title, url]))
            if value:
                parts.append(Paragraph(f"Reglementen: {_escape(value)}", styles["body"]))
        for remark in raw.get("remarks", []):
            if remark:
                parts.append(Paragraph(f"Opmerkingen: {_escape(remark)}", styles["body"]))
    if result.get("pep") is not None:
        if entity.get("schema"):
            parts.append(Paragraph(f"Schema: {_escape(entity['schema'])}", styles["body"]))
        for when in entity.get("birth_dates") or []:
            parts.append(Paragraph(f"Geboortedata/-plaats: {_escape(when)}", styles["body"]))
        for where in entity.get("birth_places") or []:
            parts.append(Paragraph(f"Geboortedata/-plaats: {_escape(where)}", styles["body"]))
        for country in entity.get("citizenships") or []:
            parts.append(Paragraph(f"Nationaliteit: {_escape(country)}", styles["body"]))
        for party in entity.get("political") or []:
            parts.append(Paragraph(f"Partij/functie: {_escape(party)}", styles["body"]))
        for pos in (entity.get("positions") or [])[:5]:
            period = f"{pos.get('start', '')}-{pos.get('end', '')}"
            parts.append(Paragraph(f"Functies: {_escape(pos.get('role', ''))} ({_escape(pos.get('status', ''))}, {_escape(period)})", styles["body"]))
        for tag in entity.get("topics") or []:
            parts.append(Paragraph(f"Risico-tags: {_escape(tag)}", styles["body"]))
        for ds in result["pep"].get("datasets", []):
            parts.append(Paragraph(f"Bron: {_escape(ds.get('title', ''))} ({_escape((ds.get('country') or '').upper())}) — {_escape(ds.get('url', ''))}", styles["body"]))
        parts.append(Paragraph(f"Details: {_escape(result['pep'].get('url', ''))}", styles["body"]))
    if result.get("opensanctions") is not None:
        os_result = result["opensanctions"]
        match = os_result.get("match")
        if match is not None:
            parts.append(Paragraph(f"Match-status: {_escape('match' if match else 'geen match')}", styles["body"]))
        for tag in (os_result.get("properties") or {}).get("topics", []):
            parts.append(Paragraph(f"Risico-tags: {_escape(tag)}", styles["body"]))
        for key, val in (os_result.get("explanations") or {}).items():
            if (val or {}).get("score", 0) > 0:
                parts.append(Paragraph(f"&bull; explanations: {_escape(key)} (score {_escape(round((val.get('score') or 0) * 100))})", styles["body"]))
        os_datasets = os_result.get("datasets") or []
        if os_datasets:
            parts.append(Paragraph(f"Bronnen: {_escape(', '.join(os_datasets))}", styles["body"]))
        if os_result.get("url"):
            parts.append(Paragraph(f"Details: {_escape(os_result.get('url'))}", styles["body"]))
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
    if meta.get("last_modified"):
        story.append(Paragraph(f"EU-lijst laatste wijziging: {_escape(meta['last_modified'])}", body))
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


_EXPORT_HEADERS = ["naam", "score", "bron", "datasets", "match-details", "eu_referentie", "geboortedata", "nationaliteit", "links"]
_EXPORT_BRONLABELS = {"eu": "EU", "pep": "PEP", "opensanctions": "OpenSanctions"}
_EXPORT_COLUMN_WIDTHS = {"A": 28, "B": 8, "C": 14, "D": 30, "E": 42, "F": 18, "G": 26, "H": 18, "I": 46}


def _export_rows(results: list[dict]) -> list[list[str]]:
    rows = []
    for result in results:
        entity = result.get("entity") or {}
        source = result.get("source", "")
        pep = result.get("pep")
        eu = result.get("eu")
        os_result = result.get("opensanctions")
        details = []
        datasets = []
        birth_dates = []
        citizenships = []
        link = ""
        if pep:
            details = [d.get("label", "") for d in (pep.get("details") or []) if d.get("label")]
            datasets = [d.get("title") or d.get("id") for d in (pep.get("datasets") or []) if d]
            birth_dates = entity.get("birth_dates") or []
            citizenships = entity.get("citizenships") or []
            link = pep.get("url", "")
        elif eu:
            details = [d.get("label", "") for d in (eu.get("details") or []) if d.get("label")]
            birth_dates = [b.get("date") or b.get("year") for b in (entity.get("birthdates") or []) if isinstance(b, dict)]
            citizenships = [c.get("description") or c.get("iso2") for c in (entity.get("citizenships") or []) if isinstance(c, dict)]
        elif os_result:
            datasets = os_result.get("datasets") or []
            birth_dates = [b.get("date") or b.get("year") for b in (entity.get("birthdates") or []) if isinstance(b, dict)]
            citizenships = [c.get("description") or c.get("iso2") for c in (entity.get("citizenships") or []) if isinstance(c, dict)]
            link = os_result.get("url", "")
        rows.append([
            entity.get("name", ""),
            str(result.get("score", 0)),
            _EXPORT_BRONLABELS.get(source, source),
            ";".join(str(x) for x in datasets if x),
            ";".join(str(x) for x in details if x),
            str(entity.get("eu_reference_number", "")),
            "/".join(str(x) for x in birth_dates if x),
            ";".join(str(x) for x in citizenships if x),
            str(link),
        ])
    return rows


def render_search_csv(results: list[dict], query: dict) -> str:
    output = StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(_EXPORT_HEADERS)
    writer.writerows(_export_rows(results))
    return output.getvalue()


def render_search_xlsx(results: list[dict], query: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Screening"
    for col, header in enumerate(_EXPORT_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
    for row in _export_rows(results):
        ws.append(row)
    for ref, width in _EXPORT_COLUMN_WIDTHS.items():
        ws.column_dimensions[ref].width = width
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def render_batch_pdf(job: dict, results: list[dict], meta: dict) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=_TITLE)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    styles.add(body)
    story = [
        Paragraph(f"<b>{_TITLE} — Batchscreeningsrapport</b>", styles["Title"]),
        Spacer(1, 4 * mm),
    ]
    story.append(Paragraph("<b>Batch</b>", styles["h2"]))
    story.append(Paragraph(f"Batch-id: {_escape(job.get('id', ''))}", body))
    story.append(Paragraph(f"Aangemaakt: {_escape(job.get('created_at', ''))}", body))
    if job.get("finished_at"):
        story.append(Paragraph(f"Voltooid: {_escape(job['finished_at'])}", body))
    story.append(Paragraph(f"Status: {_escape(job.get('status', ''))}", body))
    story.append(Paragraph(f"Regels: {job.get('total', 0)} | verwerkt: {job.get('progress', 0)}", body))
    if job.get("error_text"):
        story.append(Paragraph(f"Fout: {_escape(job['error_text'])}", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("<b>Dataversies</b>", styles["h2"]))
    meta = meta or {}
    story.append(Paragraph(f"EU-lijst generatie: {_escape(meta.get('generation_date', 'onbekend'))}", body))
    if meta.get("last_modified"):
        story.append(Paragraph(f"EU-lijst laatste wijziging: {_escape(meta['last_modified'])}", body))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("<b>Resultaten per regel</b>", styles["h2"]))
    if not results:
        story.append(Paragraph("Geen regels gevonden.", body))
    for item in results:
        row = item.get("row") or {}
        matches = item.get("matches") or []
        story.append(Paragraph(f"Regel {item.get('row_index', 0) + 1}: <b>{_escape(row.get('naam', ''))}</b>", styles["h2"]))
        if not matches:
            story.append(Paragraph("Geen overeenkomsten.", body))
        for match in matches:
            story.extend(_result_paragraphs(match, styles))
        story.append(Spacer(1, 2 * mm))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"<i>{_DISCLAIMER}</i>", body))
    doc.build(story)
    return buffer.getvalue()


def render_batch_csv(job: dict, results: list[dict]) -> str:
    output = StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(["regel", "naam-invoer", *_EXPORT_HEADERS])
    for item in results:
        row = item.get("row") or {}
        matches = item.get("matches") or []
        input_name = row.get("naam", "")
        for match in matches:
            exported = _export_rows([match])[0]
            writer.writerow([item.get("row_index", 0) + 1, input_name, *exported])
        if not matches:
            writer.writerow([item.get("row_index", 0) + 1, input_name])
    return output.getvalue()
