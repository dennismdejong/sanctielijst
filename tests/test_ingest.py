from app.ingest import parse_export

FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export" generationDate="2026-07-28T11:43:32+02:00" globalFileId="1">
  <sanctionEntity logicalId="L1" euReferenceNumber="EU.471.56" designationDate="2001-02-01" unitedNationId="TAL123">
    <subjectType code="person" classificationCode="P"/>
    <nameAlias firstName="Abdul" lastName="Hazem" wholeName="Abdul Hai Hazem Abdul Qader" strong="true" function="Diplomat"/>
    <nameAlias wholeName="Abdul Hai Hazem" strong="false"/>
    <citizenship countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <birthdate year="1971" birthdate="1971-02-15" place="Kabul" countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <address city="Kabul" street="Main St" region="Kabul Province" countryIso2Code="AF" countryDescription="AFGHANISTAN"/>
    <identification number="D123" identificationTypeCode="passport" identificationTypeDescription="National passport" countryIso2Code="AF"/>
    <regulation numberTitle="2001/154/CFSP" publicationDate="2001-02-27" programme="AFG">
      <publicationUrl>https://eur-lex.europa.eu/example</publicationUrl>
    </regulation>
    <remark>Some remarks text</remark>
  </sanctionEntity>
  <sanctionEntity logicalId="L2" euReferenceNumber="EU.2" designationDate="2022-03-09">
    <subjectType code="enterprise" classificationCode="E"/>
    <nameAlias wholeName="Rosneft" strong="true"/>
    <citizenship countryIso2Code="RU" countryDescription="RUSSIAN FEDERATION"/>
  </sanctionEntity>
</export>
"""


def test_parse_export_person():
    entities = parse_export(FIXTURE)
    assert len(entities) == 2
    person = entities[0]
    assert person["logical_id"] == "L1"
    assert person["eu_reference_number"] == "EU.471.56"
    assert person["united_nations_id"] == "TAL123"
    assert person["designation_date"] == "2001-02-01"
    assert person["subject_type"] == "person"
    assert person["aliases"] == [
        {"whole_name": "Abdul Hai Hazem Abdul Qader", "first_name": "Abdul", "last_name": "Hazem", "strong": True, "function": "Diplomat", "title": ""},
        {"whole_name": "Abdul Hai Hazem", "first_name": "", "last_name": "", "strong": False, "function": "", "title": ""},
    ]
    assert person["citizenships"] == [{"iso2": "AF", "description": "AFGHANISTAN"}]
    assert person["birthdates"] == [{"date": "1971-02-15", "year": 1971, "year_from": None, "year_to": None, "city": "", "place": "Kabul", "iso2": "AF", "country": "AFGHANISTAN"}]
    assert person["addresses"] == [{"city": "Kabul", "street": "Main St", "region": "Kabul Province", "iso2": "AF", "country": "AFGHANISTAN"}]
    assert person["identifications"] == [{"number": "D123", "type_code": "passport", "type_description": "National passport", "iso2": "AF"}]
    assert person["regulations"] == [{"number_title": "2001/154/CFSP", "publication_date": "2001-02-27", "programme": "AFG", "publication_url": "https://eur-lex.europa.eu/example"}]
    assert person["remarks"] == ["Some remarks text"]


def test_parse_export_enterprise():
    entities = parse_export(FIXTURE)
    ent = entities[1]
    assert ent["subject_type"] == "enterprise"
    assert ent["aliases"][0]["whole_name"] == "Rosneft"
    assert ent["aliases"][0]["strong"] is True
    assert ent["citizenships"] == [{"iso2": "RU", "description": "RUSSIAN FEDERATION"}]
    assert ent["birthdates"] == []
