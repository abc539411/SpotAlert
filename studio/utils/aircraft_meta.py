"""
Aircraft type → manufacturer/family derivation — shared by app.py's catalog
stats endpoints and utils/fr24_lookup.py (FR24's aircraft-detail response
gives a model string but no manufacturer field, so this regex table fills
that in, the same way it already does for photos already in the Lightroom
catalog).
"""
import re as _re


def aircraft_manufacturer(type_name: str) -> str:
    if not type_name: return ''
    t = type_name.upper().strip()
    # Leonardo/AgustaWestland — before Airbus (A109/A119/A129 start with A+digit)
    if _re.match(r'^AW\d{3}', t):                                              return 'Leonardo'
    if _re.match(r'^AB[- ]?\d{2,3}', t):                                       return 'Leonardo'  # AB206, AB412
    if _re.match(r'^A1[0-3]\d\b', t):                                          return 'Leonardo'  # A109, A119, A129
    if _re.search(r'\b(WILDCAT|LYNX|MERLIN)\b', t) or _re.match(r'^EH-101', t): return 'Leonardo'
    # Airbus narrowbody/widebody
    if _re.match(r'^A\d{3}', t):                                               return 'Airbus'
    if _re.match(r'^7\d{2}', t) or 'DREAMLINER' in t:                         return 'Boeing'
    if _re.match(r'^(ERJ|E1[679]\d|E[23]\d{2}|190-|170-|175-|195-)', t):      return 'Embraer'
    if _re.match(r'^(CRJ|BD-|Q[234]\d{2})', t) or 'LEARJET' in t:             return 'Bombardier'
    if 'DASH 8' in t or t.startswith('DHC'):                                   return 'De Havilland'
    if t.startswith('ATR') or _re.match(r'^(42|72)-\d', t):                    return 'ATR'
    if _re.match(r'^(MD|DC)-', t):                                             return 'McDonnell Douglas'
    # Boeing military (B-2 Spirit is Northrop Grumman, not here)
    if _re.match(r'^(C-17|C-32|C-40|B-17|B-29|B-52|B-1\b|F-15|F/?A-18|CF-188|KC-135|KC-46|E-3\b|E-4\b|E-6\b|P-8)', t): return 'Boeing'
    if _re.match(r'^(AH-64|CH-47)', t):                                        return 'Boeing'
    # Lockheed Martin
    if _re.match(r'^(C-130|C-5(?!\d)|P-3|S-3\b|F-16|F-22|F-35|U-2|SR-71|TR-1|U-2)', t): return 'Lockheed Martin'
    if 'HERCULES' in t or _re.match(r'^SP-\d', t):                            return 'Lockheed Martin'
    # Saab
    if _re.match(r'^340[A-Z]?\b', t) or 'SAAB' in t:                          return 'Saab'
    if _re.match(r'^JAS\s*39|GRIPEN', t):                                      return 'Saab'
    # BAE Systems — \bHAWK\b word boundary prevents SEAHAWK false match
    if _re.search(r'\bHAWK\b', t) or _re.match(r'^BAE?\s', t) or _re.match(r'^BAC\s', t): return 'BAE Systems'
    if _re.match(r'^146\b|^RJ[0-9]', t):                                       return 'BAE Systems'  # BAe 146 / Avro RJ
    # Douglas historical
    if _re.match(r'^(C-47|C-54)', t) or 'SKYTRAIN' in t or 'SKYMASTER' in t:  return 'Douglas'
    if _re.match(r'^A-4\b|^A-26\b', t):                                        return 'Douglas'
    # Airbus Helicopters (Eurocopter)
    if _re.match(r'^(EC\s?\d{3}|H[1-4]\d{2}|AS\s?\d{3}|MRH-?90)', t):        return 'Airbus Helicopters'
    # Sikorsky
    if _re.match(r'^(UH-60|MH-60|SH-60|HH-60|CH-53|S-9\d)', t) or 'SEAHAWK' in t: return 'Sikorsky'
    # Bell
    if _re.match(r'^(UH-1|AH-1|OH-58|V-22)', t) or t.startswith('BELL '):     return 'Bell'
    # Daher/Socata (TBM 7xx/8xx/9xx and TB-2x) — before Northrop Grumman TBM check
    if _re.match(r'^TBM\s*[789]\d{2}', t) or _re.match(r'^TB-?\s*\d{2}\b', t): return 'Daher'
    # Northrop Grumman (historical Grumman + B-2 Spirit)
    if _re.match(r'^(S-2[A-Z]?\b|TBF|TBM|B-2\b|F-14|E-2\b|C-2\b|EA-6|T-38|F-5\b)', t): return 'Northrop Grumman'
    # CASA / Airbus Defence
    if _re.match(r'^CN-\d', t):                                                return 'Airbus'
    # COMAC (before Cessna ^C\d{3})
    if _re.match(r'^C919|^ARJ21', t) or 'COMAC' in t:                         return 'COMAC'
    # Cessna/Textron (civil C172 etc. — hyphened military types already caught above)
    if _re.match(r'^C\d{3}\b', t) or _re.search(r'CESSNA|CITATION|CARAVAN', t): return 'Cessna'
    # Beechcraft (including T-6A Texan II)
    if _re.match(r'^B[0-9]|KING AIR|BEECH|^T-6[A-Z]?\b', t):                 return 'Beechcraft'
    # Gulfstream
    if _re.match(r'^G[5-8]\d{2}\b', t) or _re.match(r'^G-?(I{1,3}V?|V)\b', t) or 'GULFSTREAM' in t: return 'Gulfstream'
    # Hawker (Raytheon/Hawker Beechcraft bizjets — distinct from BAE Hawk jet trainer)
    if _re.match(r'^HAWKER\b', t):                                             return 'Hawker'
    # Pilatus
    if _re.match(r'^PC-', t):                                                  return 'Pilatus'
    # Dassault
    if 'FALCON' in t or 'MIRAGE' in t or 'RAFALE' in t:                       return 'Dassault'
    # Fokker (F27/F28/F50/F70/F100 stored without hyphen; F-100 Super Sabre has hyphen)
    if _re.match(r'^F(27|28|50|70|100)\b', t) or 'FOKKER' in t:               return 'Fokker'
    # CAIG / Chengdu
    if _re.match(r'^J\d{1,2}[A-Z]', t):                                        return 'CAIG'
    # Piper
    if _re.match(r'^PA-\d', t) or 'PIPER' in t:                               return 'Piper'
    # Robinson Helicopter
    if _re.match(r'^R-?(22|44|66)\b', t) or 'ROBINSON' in t:                  return 'Robinson'
    # Antonov
    if _re.match(r'^AN-\d', t) or 'ANTONOV' in t:                             return 'Antonov'
    # Ilyushin
    if _re.match(r'^IL-\d', t) or 'ILYUSHIN' in t:                            return 'Ilyushin'
    # Sukhoi / UAC (Superjet)
    if _re.match(r'^SU-\d', t) or 'SUPERJET' in t or 'SUKHOI' in t:          return 'Sukhoi'
    # Cirrus (SR-71 already caught by Lockheed Martin above)
    if _re.match(r'^SR-?\d{2}\b', t) or 'CIRRUS' in t:                        return 'Cirrus'
    # Diamond Aircraft
    if _re.match(r'^D[AV]\d{2}', t) or 'DIAMOND' in t:                        return 'Diamond'
    # North American Aviation (vintage)
    if _re.match(r'^(P-51|B-25|F-86|F-100)', t) or 'MUSTANG' in t:           return 'North American'
    # General Dynamics
    if _re.match(r'^F-111', t):                                                return 'General Dynamics'
    # Fairchild Republic (A-10 Thunderbolt II)
    if _re.match(r'^A-10\b', t) or 'FAIRCHILD' in t:                          return 'Fairchild'
    # Rockwell (OV-10 Bronco, B-1 already caught as Boeing above)
    if _re.match(r'^OV-10', t):                                                return 'Rockwell'
    return ''


def aircraft_family(type_name: str) -> str:
    """Strip variant suffixes to return the aircraft family name for grouping."""
    if not type_name:
        return type_name
    t = type_name.strip()
    # Airbus: A320-232 → A320, A330-343 → A330 (A400M has no hyphen so stays as-is)
    m = _re.match(r'^(A\d{3})[-\s]', t)
    if m: return m.group(1)
    # Boeing/Embraer 3-digit: 787-9 → 787, 737-838 → 737, 190-100STD → 190
    m = _re.match(r'^(\d{3})-', t)
    if m: return m.group(1)
    # Hyphenated military/tactical: F-35A → F-35, C-130H → C-130, AH-64D → AH-64, ERJ-190 → ERJ-190
    m = _re.match(r'^([A-Z]{1,3}-\d+)', t)
    if m: return m.group(1)
    # Embraer E-jets with E prefix: E190-100STD → E190
    m = _re.match(r'^(E\d{3})[-\s]', t)
    if m: return m.group(1)
    # Alphanumeric + space + digits: BAe 146-200 → BAe 146, EC 135T2+ → EC 135
    m = _re.match(r'^([A-Za-z]+\s+\d+)', t)
    if m: return m.group(1)
    return t
