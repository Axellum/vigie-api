"""Which country a travel advice is about: the code or the page name of each ministry, to ISO 3166-1 alpha-2.

The five ministries do not name a destination the same way (each was read on 2026-10-28, see ``tests/fixtures/travel``):

- **United States** (Department of State): ``Category`` is a FIPS 10-4 code (``IZ`` is Iraq, ``UK`` the United Kingdom,
  ``A1`` an invented one), so it is not used. The address of every advice ends with ``destination.<ISO alpha-3>.html``
  (``destination.irq``): that code is read. Five pages have another name; they are in ``US_PAGES``.
- **United Kingdom** (FCDO, GOV.UK): no code at all, only the page name (``foreign-travel-advice/cote-d-ivoire``):
  ``GB_PAGES``.
- **Canada** (Global Affairs Canada): ISO alpha-2 already, plus two parts of a country (``PT-20``, the Azores, an ISO 3166-2
  code; ``IC``, the Canary Islands, a code ISO reserves for them): ``CA_CODES``.
- **Australia** (Smartraveller): no code, only the page name (``destinations/europe/turkiye``): ``AU_PAGES``.
- **Netherlands** (Ministerie van Buitenlandse Zaken): ISO alpha-3, plus ``XKX`` (Kosovo) and ``BQ-BO``, ``BQ-SA``, ``BQ-SE``
  (Bonaire, Saba, Sint Eustatius): ``NL_CODES``.

The alpha-3 to alpha-2 table is the one of the UN Statistics Division (M49, "Standard country or area codes",
https://unstats.un.org/unsd/methodology/m49/overview/, read on 2026-10-28: 248 codes), plus the two codes M49 does not
list and the ministries use: ``TWN`` (Taiwan, ISO 3166-1) and ``XKX`` (Kosovo, a user-assigned code, ``XK`` in alpha-2,
used by Canada and the Netherlands as by the countries of this API).

A destination none of these tables knows has **no** country: it is not placed (``TravelFeed.unplaced`` names it), never
guessed from its title. A page that covers several territories names them all (the FCDO advice "Cook Islands, Tokelau and
Niue" is the advice of each of the three); a page that covers a part of a country is the advice of that country, under its
own title (the American advice "Gaza", the Canadian "Azores").
"""

from __future__ import annotations

from typing import Optional

# ISO 3166-1 alpha-3 -> alpha-2: UN M49 (248 codes), then TWN and XKX (see the module docstring).
_ALPHA3 = """
    ABW:AW AFG:AF AGO:AO AIA:AI ALA:AX ALB:AL AND:AD ARE:AE ARG:AR ARM:AM ASM:AS ATA:AQ ATF:TF ATG:AG
    AUS:AU AUT:AT AZE:AZ BDI:BI BEL:BE BEN:BJ BES:BQ BFA:BF BGD:BD BGR:BG BHR:BH BHS:BS BIH:BA BLM:BL
    BLR:BY BLZ:BZ BMU:BM BOL:BO BRA:BR BRB:BB BRN:BN BTN:BT BVT:BV BWA:BW CAF:CF CAN:CA CCK:CC CHE:CH
    CHL:CL CHN:CN CIV:CI CMR:CM COD:CD COG:CG COK:CK COL:CO COM:KM CPV:CV CRI:CR CUB:CU CUW:CW CXR:CX
    CYM:KY CYP:CY CZE:CZ DEU:DE DJI:DJ DMA:DM DNK:DK DOM:DO DZA:DZ ECU:EC EGY:EG ERI:ER ESH:EH ESP:ES
    EST:EE ETH:ET FIN:FI FJI:FJ FLK:FK FRA:FR FRO:FO FSM:FM GAB:GA GBR:GB GEO:GE GGY:GG GHA:GH GIB:GI
    GIN:GN GLP:GP GMB:GM GNB:GW GNQ:GQ GRC:GR GRD:GD GRL:GL GTM:GT GUF:GF GUM:GU GUY:GY HKG:HK HMD:HM
    HND:HN HRV:HR HTI:HT HUN:HU IDN:ID IMN:IM IND:IN IOT:IO IRL:IE IRN:IR IRQ:IQ ISL:IS ISR:IL ITA:IT
    JAM:JM JEY:JE JOR:JO JPN:JP KAZ:KZ KEN:KE KGZ:KG KHM:KH KIR:KI KNA:KN KOR:KR KWT:KW LAO:LA LBN:LB
    LBR:LR LBY:LY LCA:LC LIE:LI LKA:LK LSO:LS LTU:LT LUX:LU LVA:LV MAC:MO MAF:MF MAR:MA MCO:MC MDA:MD
    MDG:MG MDV:MV MEX:MX MHL:MH MKD:MK MLI:ML MLT:MT MMR:MM MNE:ME MNG:MN MNP:MP MOZ:MZ MRT:MR MSR:MS
    MTQ:MQ MUS:MU MWI:MW MYS:MY MYT:YT NAM:NA NCL:NC NER:NE NFK:NF NGA:NG NIC:NI NIU:NU NLD:NL NOR:NO
    NPL:NP NRU:NR NZL:NZ OMN:OM PAK:PK PAN:PA PCN:PN PER:PE PHL:PH PLW:PW PNG:PG POL:PL PRI:PR PRK:KP
    PRT:PT PRY:PY PSE:PS PYF:PF QAT:QA REU:RE ROU:RO RUS:RU RWA:RW SAU:SA SDN:SD SEN:SN SGP:SG SGS:GS
    SHN:SH SJM:SJ SLB:SB SLE:SL SLV:SV SMR:SM SOM:SO SPM:PM SRB:RS SSD:SS STP:ST SUR:SR SVK:SK SVN:SI
    SWE:SE SWZ:SZ SXM:SX SYC:SC SYR:SY TCA:TC TCD:TD TGO:TG THA:TH TJK:TJ TKL:TK TKM:TM TLS:TL TON:TO
    TTO:TT TUN:TN TUR:TR TUV:TV TZA:TZ UGA:UG UKR:UA UMI:UM URY:UY USA:US UZB:UZ VAT:VA VCT:VC VEN:VE
    VGB:VG VIR:VI VNM:VN VUT:VU WLF:WF WSM:WS YEM:YE ZAF:ZA ZMB:ZM ZWE:ZW
    TWN:TW XKX:XK
"""
ALPHA3_TO_ALPHA2: dict[str, str] = {pair[:3]: pair[4:] for pair in _ALPHA3.split()}

# The American pages whose name is not ``destination.<alpha-3>``: the page -> the countries it is about. ``None``: a page
# that is not placed (the "French West Indies" page, an older page beside the pages of Guadeloupe, Martinique, Saint
# Barthélemy and Saint Martin, which each have their own).
US_PAGES: dict[str, Optional[str]] = {
    "destination.xgz": "PS",  # Gaza
    "destination.xwb": "PS",  # The West Bank
    "destination.xks": "XK",  # Kosovo
    "destination.xqx": "BQ",  # Saba and Sint Eustatius
    "tonga-travel-advisory": "TO",  # a second page for Tonga, beside destination.ton
    "french-west-indies-travel-advisory": None,
}

# The pages of the FCDO (``foreign-travel-advice/<page>``) -> ISO alpha-2 (several codes, separated by a space, when the
# page covers several territories).
GB_PAGES: dict[str, str] = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "andorra": "AD", "angola": "AO", "anguilla": "AI",
    "antarctica-british-antarctic-territory": "AQ", "antigua-and-barbuda": "AG", "argentina": "AR", "armenia": "AM",
    "aruba": "AW", "australia": "AU", "austria": "AT", "azerbaijan": "AZ", "bahamas": "BS", "bahrain": "BH",
    "bangladesh": "BD", "barbados": "BB", "belarus": "BY", "belgium": "BE", "belize": "BZ", "benin": "BJ",
    "bermuda": "BM", "bhutan": "BT", "bolivia": "BO", "bonaire-st-eustatius-saba": "BQ", "bosnia-and-herzegovina": "BA",
    "botswana": "BW", "brazil": "BR", "british-indian-ocean-territory": "IO", "british-virgin-islands": "VG",
    "brunei": "BN", "bulgaria": "BG", "burkina-faso": "BF", "burundi": "BI", "cambodia": "KH", "cameroon": "CM",
    "canada": "CA", "cape-verde": "CV", "cayman-islands": "KY", "central-african-republic": "CF", "chad": "TD",
    "chile": "CL", "china": "CN", "colombia": "CO", "comoros": "KM", "congo": "CG", "cook-islands-tokelau-and-niue": "CK TK NU",
    "costa-rica": "CR", "cote-d-ivoire": "CI", "croatia": "HR", "cuba": "CU", "curacao": "CW", "cyprus": "CY",
    "czechia": "CZ", "democratic-republic-of-the-congo": "CD", "denmark": "DK", "djibouti": "DJ", "dominica": "DM",
    "dominican-republic": "DO", "ecuador": "EC", "egypt": "EG", "el-salvador": "SV", "equatorial-guinea": "GQ",
    "eritrea": "ER", "estonia": "EE", "eswatini": "SZ", "ethiopia": "ET", "falkland-islands": "FK",
    "federated-states-of-micronesia": "FM", "fiji": "FJ", "finland": "FI", "france": "FR", "french-guiana": "GF",
    "french-polynesia": "PF", "gabon": "GA", "georgia": "GE", "germany": "DE", "ghana": "GH", "gibraltar": "GI",
    "greece": "GR", "grenada": "GD", "guadeloupe": "GP", "guatemala": "GT", "guinea": "GN", "guinea-bissau": "GW",
    "guyana": "GY", "haiti": "HT", "honduras": "HN", "hong-kong": "HK", "hungary": "HU", "iceland": "IS",
    "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ", "ireland": "IE", "israel": "IL", "italy": "IT",
    "jamaica": "JM", "japan": "JP", "jordan": "JO", "kazakhstan": "KZ", "kenya": "KE", "kiribati": "KI",
    "kosovo": "XK", "kuwait": "KW", "kyrgyzstan": "KG", "laos": "LA", "latvia": "LV", "lebanon": "LB",
    "lesotho": "LS", "liberia": "LR", "libya": "LY", "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU",
    "macao": "MO", "madagascar": "MG", "malawi": "MW", "malaysia": "MY", "maldives": "MV", "mali": "ML",
    "malta": "MT", "marshall-islands": "MH", "martinique": "MQ", "mauritania": "MR", "mauritius": "MU",
    "mayotte": "YT", "mexico": "MX", "moldova": "MD", "monaco": "MC", "mongolia": "MN", "montenegro": "ME",
    "montserrat": "MS", "morocco": "MA", "mozambique": "MZ", "myanmar": "MM", "namibia": "NA", "nauru": "NR",
    "nepal": "NP", "netherlands": "NL", "new-caledonia": "NC", "new-zealand": "NZ", "nicaragua": "NI", "niger": "NE",
    "nigeria": "NG", "north-korea": "KP", "north-macedonia": "MK", "norway": "NO", "oman": "OM", "pakistan": "PK",
    "palau": "PW", "palestine": "PS", "panama": "PA", "papua-new-guinea": "PG", "paraguay": "PY", "peru": "PE",
    "philippines": "PH", "pitcairn-island": "PN", "poland": "PL", "portugal": "PT", "qatar": "QA", "reunion": "RE",
    "romania": "RO", "russia": "RU", "rwanda": "RW", "samoa": "WS", "san-marino": "SM", "sao-tome-and-principe": "ST",
    "saudi-arabia": "SA", "senegal": "SN", "serbia": "RS", "seychelles": "SC", "sierra-leone": "SL",
    "singapore": "SG", "slovakia": "SK", "slovenia": "SI", "solomon-islands": "SB", "somalia": "SO",
    "south-africa": "ZA", "south-georgia-and-south-sandwich-islands": "GS", "south-korea": "KR", "south-sudan": "SS",
    "spain": "ES", "sri-lanka": "LK", "st-helena-ascension-and-tristan-da-cunha": "SH", "st-kitts-and-nevis": "KN",
    "st-lucia": "LC", "st-maarten": "SX", "st-martin-and-st-barthelemy": "MF BL", "st-pierre-and-miquelon": "PM",
    "st-vincent-and-the-grenadines": "VC", "sudan": "SD", "suriname": "SR", "sweden": "SE", "switzerland": "CH",
    "syria": "SY", "taiwan": "TW", "tajikistan": "TJ", "tanzania": "TZ", "thailand": "TH", "the-gambia": "GM",
    "timor-leste": "TL", "togo": "TG", "tonga": "TO", "trinidad-and-tobago": "TT", "tunisia": "TN", "turkey": "TR",
    "turkmenistan": "TM", "turks-and-caicos-islands": "TC", "tuvalu": "TV", "uganda": "UG", "ukraine": "UA",
    "united-arab-emirates": "AE", "uruguay": "UY", "usa": "US", "uzbekistan": "UZ", "vanuatu": "VU",
    "venezuela": "VE", "vietnam": "VN", "wallis-and-futuna": "WF", "western-sahara": "EH", "yemen": "YE",
    "zambia": "ZM", "zimbabwe": "ZW",
}  # fmt: skip

# The pages of Smartraveller (``destinations/<region>/<page>``) -> ISO alpha-2.
AU_PAGES: dict[str, str] = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "angola": "AO", "argentina": "AR", "armenia": "AM",
    "austria": "AT", "azerbaijan": "AZ", "bahamas": "BS", "bahrain": "BH", "bangladesh": "BD", "belarus": "BY",
    "belgium": "BE", "belize": "BZ", "benin": "BJ", "bhutan": "BT", "bolivia": "BO", "bosnia-and-herzegovina": "BA",
    "botswana": "BW", "brazil": "BR", "brunei-darussalam": "BN", "bulgaria": "BG", "burkina-faso": "BF",
    "burundi": "BI", "cambodia": "KH", "cameroon": "CM", "canada": "CA", "central-african-republic": "CF",
    "chad": "TD", "chile": "CL", "china": "CN", "colombia": "CO", "cook-islands": "CK", "costa-rica": "CR",
    "cote-divoire-ivory-coast": "CI", "croatia": "HR", "cuba": "CU", "cyprus": "CY", "czech-republic": "CZ",
    "democratic-republic-congo": "CD", "denmark": "DK", "djibouti": "DJ", "dominican-republic": "DO", "ecuador": "EC",
    "egypt": "EG", "el-salvador": "SV", "eritrea": "ER", "estonia": "EE", "eswatini": "SZ", "ethiopia": "ET",
    "federated-states-micronesia": "FM", "fiji": "FJ", "finland": "FI", "france": "FR", "french-polynesia": "PF",
    "gabon": "GA", "gambia": "GM", "georgia": "GE", "germany": "DE", "ghana": "GH", "greece": "GR", "guam": "GU",
    "guatemala": "GT", "guinea": "GN", "guyana": "GY", "haiti": "HT", "honduras": "HN", "hong-kong": "HK",
    "hungary": "HU", "iceland": "IS", "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ", "ireland": "IE",
    "israel": "IL", "italy": "IT", "jamaica": "JM", "japan": "JP", "jordan": "JO", "kazakhstan": "KZ", "kenya": "KE",
    "kosovo": "XK", "kuwait": "KW", "kyrgyz-republic": "KG", "laos": "LA", "latvia": "LV", "lebanon": "LB",
    "lesotho": "LS", "liberia": "LR", "libya": "LY", "lithuania": "LT", "macau": "MO", "madagascar": "MG",
    "malawi": "MW", "malaysia": "MY", "maldives": "MV", "mali": "ML", "malta": "MT", "marshall-islands": "MH",
    "mauritania": "MR", "mauritius": "MU", "mexico": "MX", "moldova": "MD", "mongolia": "MN", "montenegro": "ME",
    "morocco": "MA", "mozambique": "MZ", "myanmar": "MM", "namibia": "NA", "nepal": "NP", "netherlands": "NL",
    "new-caledonia": "NC", "new-zealand": "NZ", "nicaragua": "NI", "niger": "NE", "nigeria": "NG", "niue": "NU",
    "north-korea-democratic-peoples-republic-korea": "KP", "north-macedonia": "MK", "norway": "NO", "oman": "OM",
    "pakistan": "PK", "palau": "PW", "palestine": "PS", "panama": "PA", "papua-new-guinea": "PG", "paraguay": "PY",
    "peru": "PE", "philippines": "PH", "poland": "PL", "portugal": "PT", "qatar": "QA", "romania": "RO",
    "russia": "RU", "rwanda": "RW", "samoa": "WS", "saudi-arabia": "SA", "senegal": "SN", "serbia": "RS",
    "seychelles": "SC", "sierra-leone": "SL", "singapore": "SG", "slovakia": "SK", "slovenia": "SI",
    "solomon-islands": "SB", "somalia": "SO", "south-africa": "ZA", "south-korea-republic-korea": "KR",
    "south-sudan": "SS", "spain": "ES", "sri-lanka": "LK", "sudan": "SD", "sweden": "SE", "switzerland": "CH",
    "syria": "SY", "taiwan": "TW", "tajikistan": "TJ", "tanzania": "TZ", "thailand": "TH", "timor-leste": "TL",
    "tonga": "TO", "trinidad-and-tobago": "TT", "tunisia": "TN", "turkiye": "TR", "turkmenistan": "TM",
    "uganda": "UG", "ukraine": "UA", "united-arab-emirates": "AE", "united-kingdom": "GB",
    "united-states-america": "US", "uruguay": "UY", "uzbekistan": "UZ", "vanuatu": "VU", "venezuela": "VE",
    "vietnam": "VN", "yemen": "YE", "zambia": "ZM", "zimbabwe": "ZW",
}  # fmt: skip

# Pages of the Smartraveller export that are not a destination (read on 2026-10-28): skipped, not counted as unplaced.
AU_NOT_DESTINATIONS = frozenset({"crisis-alert", "no-travel-advice"})

# Canada: the codes that are not a plain ISO alpha-2 country -> the country they are part of.
CA_CODES: dict[str, str] = {"PT-20": "PT", "IC": "ES"}

# The Netherlands: the codes that are not in ``ALPHA3_TO_ALPHA2``.
NL_CODES: dict[str, str] = {"BQ-BO": "BQ", "BQ-SA": "BQ", "BQ-SE": "BQ"}


def _codes(value: Optional[str]) -> tuple[str, ...]:
    return tuple(value.split()) if value else ()


def us_countries(page: str) -> tuple[str, ...]:
    """The countries of an American page (``destination.irq``), ``()`` when it is not placed."""
    if page in US_PAGES:
        return _codes(US_PAGES[page])
    if page.startswith("destination.") and len(page) == len("destination.") + 3:
        return _codes(ALPHA3_TO_ALPHA2.get(page.removeprefix("destination.").upper()))
    return ()


def gb_countries(page: str) -> tuple[str, ...]:
    """The countries of an FCDO page (``cote-d-ivoire``), ``()`` when it is not placed."""
    return _codes(GB_PAGES.get(page))


def au_countries(page: str) -> tuple[str, ...]:
    """The countries of a Smartraveller page (``turkiye``), ``()`` when it is not placed."""
    return _codes(AU_PAGES.get(page))


def ca_countries(code: str) -> tuple[str, ...]:
    """The country of a Canadian code (``MX``, ``PT-20``), ``()`` when it is not one."""
    if code in CA_CODES:
        return (CA_CODES[code],)
    return (code,) if len(code) == 2 and code.isalpha() and code.isupper() else ()


def nl_countries(code: str) -> tuple[str, ...]:
    """The country of a Dutch code (``MEX``, ``XKX``, ``BQ-SA``), ``()`` when it is not one."""
    return _codes(NL_CODES.get(code) or ALPHA3_TO_ALPHA2.get(code))
