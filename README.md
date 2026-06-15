# Inaktivering af CVR-leverandører i Nexus

Automatisering der validerer aktive leverandører i KMD Nexus mod CVR-registret og inaktiverer leverandører, der ikke længere kan bekræftes.

## Hvad gør robotten?

1. **Henter aktive leverandører** fra KMD Nexus — kun dem med et CVR-nummer og status aktiv
2. **Slår CVR-oplysninger op** via Datafordeleren for hver leverandør
3. **Kontrollerer P-nummer** — leverandører uden P-nummer i Nexus sendes til manuel behandling
4. **Sammenligner navne** med Levenshtein-afstand: Nexus-navn sammenlignes først med virksomhedsnavnet fra CVR. Hvis det ikke matcher, forsøges P-enhedsnavnet som fallback. Afvigelse på 10% eller mere sendes til manuel behandling
5. **Inaktiverer** leverandører der ikke kan bekræftes i CVR

## Forudsætninger

- Python ≥ 3.13
- [`uv`](https://docs.astral.sh/uv/) til pakkehåndtering
- Adgang til **Automation Server** (arbejdskø)
- Adgang til **KMD Nexus** (produktion)
- Adgang til **Datafordeleren** med gyldigt klientcertifikat
- En **Odense SQL Server**-konto til tracking

## Installation

```sh
uv sync
```

## Konfiguration

Kopiér `.env.example` til `.env` og udfyld følgende:

| Variabel | Beskrivelse |
|---|---|
| `CERTIFIKATER` | Sti til mappe med `datafordeler.crt` og `datafordeler.key` |
| *(Automation Server-variabler)* | Ifølge `automation-server-client`-dokumentationen |

## Kørsel

```sh
# Fyld arbejdskøen med aktive leverandører fra Nexus
uv run python main.py --queue

# Behandl arbejdskøen
uv run python main.py
```

### Argumenter

| Argument | Beskrivelse |
|---|---|
| `--queue` | Fyld arbejdskøen og afslut (kør ingen behandling) |

## Afhængigheder

| Pakke | Formål |
|---|---|
| `automation-server-client` | Arbejdskø-håndtering |
| `kmd-nexus-client` | Integration med KMD Nexus |
| `datafordeler` | CVR-opslag via Datafordeleren |
| `odk-tools` | Aktivitetssporing og rapportering |
| `rapidfuzz` | Levenshtein-afstandsberegning |

## Persondatasikkerhed

CVR-numre og P-numre er erhvervsidentifikatorer og er ikke personoplysninger for juridiske enheder. Robotten behandler ikke CPR-numre eller andre personoplysninger.

- Ingen legitimationsoplysninger må lægges i dette repository
- `.env` og `certifikater/`-mappen er ekskluderet via `.gitignore` og må aldrig committes
- Legitimationsoplysninger håndteres udelukkende via miljøvariabler (`.env`) og Automation Server Credentials

