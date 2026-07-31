import xml.etree.ElementTree as ET

NS = {"fsd": "http://eu.europa.ec/fpi/fsd/export"}


def _to_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_export(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entities = []
    for se in root.findall("fsd:sanctionEntity", NS):
        st = se.find("fsd:subjectType", NS)
        subject_code = st.get("code", "") if st is not None else ""
        aliases = []
        for na in se.findall("fsd:nameAlias", NS):
            aliases.append({
                "whole_name": na.get("wholeName", "").strip(),
                "first_name": na.get("firstName", "").strip(),
                "last_name": na.get("lastName", "").strip(),
                "strong": na.get("strong", "false") == "true",
                "function": na.get("function", "").strip(),
                "title": na.get("title", "").strip(),
            })
        citizenships = []
        for c in se.findall("fsd:citizenship", NS):
            citizenships.append({
                "iso2": c.get("countryIso2Code", "").strip().upper(),
                "description": c.get("countryDescription", "").strip().upper(),
            })
        birthdates = []
        for b in se.findall("fsd:birthdate", NS):
            birthdates.append({
                "date": b.get("birthdate", "").strip(),
                "year": _to_int(b.get("year", "")),
                "year_from": _to_int(b.get("yearRangeFrom", "")),
                "year_to": _to_int(b.get("yearRangeTo", "")),
                "city": b.get("city", "").strip(),
                "place": b.get("place", "").strip(),
                "iso2": b.get("countryIso2Code", "").strip().upper(),
                "country": b.get("countryDescription", "").strip().upper(),
            })
        addresses = []
        for a in se.findall("fsd:address", NS):
            addresses.append({
                "city": a.get("city", "").strip(),
                "street": a.get("street", "").strip(),
                "region": a.get("region", "").strip(),
                "iso2": a.get("countryIso2Code", "").strip().upper(),
                "country": a.get("countryDescription", "").strip().upper(),
            })
        identifications = []
        for i in se.findall("fsd:identification", NS):
            identifications.append({
                "number": i.get("number", "").strip(),
                "type_code": i.get("identificationTypeCode", "").strip(),
                "type_description": i.get("identificationTypeDescription", "").strip(),
                "iso2": i.get("countryIso2Code", "").strip().upper(),
            })
        regulations = []
        for r in se.findall("fsd:regulation", NS):
            pu = r.find("fsd:publicationUrl", NS)
            regulations.append({
                "number_title": r.get("numberTitle", "").strip(),
                "publication_date": r.get("publicationDate", "").strip(),
                "programme": r.get("programme", "").strip(),
                "publication_url": pu.text.strip() if pu is not None and pu.text else "",
            })
        remarks = []
        for rm in se.findall("fsd:remark", NS):
            if rm.text and rm.text.strip():
                remarks.append(rm.text.strip())
        entities.append({
            "logical_id": se.get("logicalId", ""),
            "eu_reference_number": se.get("euReferenceNumber", ""),
            "united_nations_id": se.get("unitedNationId", ""),
            "designation_date": se.get("designationDate", ""),
            "subject_type": "enterprise" if subject_code == "enterprise" else "person",
            "aliases": aliases,
            "citizenships": citizenships,
            "birthdates": birthdates,
            "addresses": addresses,
            "identifications": identifications,
            "regulations": regulations,
            "remarks": remarks,
        })
    return entities


def _read_generation_date(xml_bytes: bytes) -> str:
    return ET.fromstring(xml_bytes).get("generationDate", "")
