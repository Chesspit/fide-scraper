"""FIDE-Föderationscode → ISO-3166 alpha-3 für die Choropleth-Karte.

Basis war FIDE_TO_ISO3 aus frontend/data_population.py (dort mit pandas-Kontext,
deshalb hier als eigenständige Kopie), abgeglichen gegen die 206 real in
orchestrator.scrape_groups vorkommenden Föderationen (Stand 2026-07-16).

Stolperfallen: FIDE und ISO vergeben teils denselben Code verschieden —
FIDE BRN = Bahrain (ISO BHR), ISO BRN = Brunei (FIDE BRU); FIDE ANT =
Antigua & Barbuda (ISO ATG), ISO ANT war die aufgelöste Niederl. Antillen.
"""

# Föderationen, die kein Land sind (Update-Batches, staatenlos) — nie auf die Karte.
SPECIAL_NON_COUNTRY = {"FID", "NON", "P1", "P2", "P3"}

# UK-Teilverbände: auf der Karte zu GBR aggregiert (eigene FIDE-Föderationen,
# aber eine ISO-Geometrie).
UK_FEDS = {"ENG", "SCO", "WLS", "WAL", "NIR"}

# Nur Codes, die vom ISO-3166-alpha-3 abweichen oder keine Geometrie haben (None).
FIDE_TO_ISO3: dict[str, str | None] = {
    # Europa
    "GER": "DEU", "ENG": "GBR", "SCO": "GBR", "WLS": "GBR", "WAL": "GBR",
    "NIR": "GBR", "NED": "NLD", "SUI": "CHE", "DEN": "DNK", "POR": "PRT",
    "GRE": "GRC", "BUL": "BGR", "CRO": "HRV", "SLO": "SVN", "LAT": "LVA",
    "MNC": "MCO", "FAI": "FRO", "ICE": "ISL",
    "KOS": "XKX",   # Kosovo — kein offizieller ISO-Code; plotly rendert XKX ggf. nicht (Fußnote)
    "GCI": None,    # Guernsey — ISO GGY, in plotlys Natural-Earth-Basemap ohne eigene Fläche
    "JCI": None,    # Jersey  — ISO JEY, dito
    "IOM": None,    # Isle of Man — ISO IMN, dito
    # Asien
    "IRI": "IRN", "UAE": "ARE", "KSA": "SAU", "KUW": "KWT", "OMA": "OMN",
    "PLE": "PSE", "LBN": "LBN", "BRN": "BHR", "BRU": "BRN", "VIE": "VNM",
    "PHI": "PHL", "MAS": "MYS", "INA": "IDN", "SRI": "LKA", "BAN": "BGD",
    "MYA": "MMR", "CAM": "KHM", "NEP": "NPL", "BHU": "BTN", "MGL": "MNG",
    "TPE": "TWN", "MAC": "MAC", "MDV": "MDV",
    # Afrika
    "ALG": "DZA", "LBA": "LBY", "SUD": "SDN", "MTN": "MRT", "MAD": "MDG",
    "MRI": "MUS", "SEY": "SYC", "TAN": "TZA", "ZAM": "ZMB", "ZIM": "ZWE",
    "BOT": "BWA", "RSA": "ZAF", "LES": "LSO", "ANG": "AGO", "NGR": "NGA",
    "NIG": "NER", "BUR": "BFA", "TOG": "TGO", "GUI": "GIN", "GEQ": "GNQ",
    "CHA": "TCD", "CAF": "CAF", "MAW": "MWI", "GHA": "GHA", "CPV": "CPV",
    "GBS": "GNB",
    # Amerikas
    "CHI": "CHL", "URU": "URY", "PAR": "PRY", "ESA": "SLV", "GUA": "GTM",
    "HON": "HND", "NCA": "NIC", "CRC": "CRI", "HAI": "HTI", "BAR": "BRB",
    "BAH": "BHS", "ANT": "ATG", "SKN": "KNA", "VIN": "VCT", "GRN": "GRD",
    "DMA": "DMA", "PUR": "PRI", "BIZ": "BLZ", "ARU": "ABW", "BER": "BMU",
    "CAY": "CYM", "ISV": "VIR", "IVB": "VGB",
    "AHO": None,    # Niederländische Antillen — 2010 aufgelöst, keine Geometrie
    # Ozeanien
    "FIJ": "FJI", "VAN": "VUT", "SAM": "WSM", "TGA": "TON", "SOL": "SLB",
    "GUM": "GUM", "NCL": "NCL", "PLW": "PLW", "NRU": "NRU", "PNG": "PNG",
}


def fide_to_iso3(fed: str) -> str | None:
    """ISO-3166-alpha-3 zum FIDE-Code — None, wenn nicht kartierbar.

    Codes ohne Eintrag in FIDE_TO_ISO3 sind identisch mit ihrem ISO-Code
    (der Großteil: FRA, ESP, USA, IND, ...).
    """
    if fed in SPECIAL_NON_COUNTRY:
        return None
    return FIDE_TO_ISO3.get(fed, fed)
