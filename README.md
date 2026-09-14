# Amplifier_characterization

S12-Charakterisierung von RF-Verstaerkern mit dem **Rohde & Schwarz FPC1500**
(Ethernet, IP `192.168.1.187`) ueber PyVISA.

Das Skript rastert **Frequenz** und **Ausgangspegel des Tracking-Generators** ab,
misst pro Pegelstufe einen kompletten Sweep, interpoliert auf das gewuenschte
Frequenzraster und legt CSV + Plots in einem **datierten Ausgabeverzeichnis** ab.

## Installation

```bash
pip install -r requirements.txt        # Betrieb
pip install -r requirements-dev.txt    # zusaetzlich Tests
```

Der FPC1500 wird ueber **HiSLIP** angesprochen:
`TCPIP0::192.168.1.187::hislip0::INSTR`. Der SCPI-Raw-Socket auf Port 5025 ist
bei diesem Geraet nicht offen, `::inst0::INSTR` (VXI-11) laeuft in
`VI_ERROR_RSRC_NFOUND`. Mit `pyvisa-py` als Backend ist kein
NI-VISA noetig; mit installiertem R&S-/NI-VISA funktioniert es ebenso.

## Benutzung

**Auszufuehrende Datei ist immer `run_s12.py`** (im Repo-Wurzelverzeichnis).

```bash
python run_s12.py --check                 # Verbindung zum FPC1500 pruefen
python run_s12.py --probe                 # Geraete-Selbsttest (SCPI + ein Sweep)
python run_s12.py --dry-run --no-prompt   # Trockenlauf ohne Hardware
python run_s12.py                         # echte Messung
```

Ohne `-c` wird `config/default.json` genutzt.

Ablauf:

1. Config laden und Messparameter anzeigen.
2. **Wegklickbarer Hinweis**: "Bitte THRU-Kalibrierung mit den Messkabeln
   durchfuehren". Erst nach dem Schliessen startet die Messung.
   (Ohne GUI faellt das Skript auf eine ENTER-Abfrage in der Konsole zurueck.)
3. Analyzer konfigurieren, Tracking-Generator einschalten.
4. Fuer jede Amplitudenstufe: Pegel setzen, Sweep ausloesen, Trace lesen,
   auf das Frequenzraster interpolieren.
5. `s12_measurement.csv`, `config_used.json` und drei PNG-Plots schreiben.

### Optionen

| Flag | Wirkung |
|---|---|
| `-c, --config` | Pfad zur JSON-Config (Standard `config/default.json`) |
| `--check` | nur `*IDN?` abfragen und beenden (Verbindungstest mit Netzwerkdiagnose) |
| `--probe` | Geraete-Selbsttest: alle SCPI-Kommandos und ein Sweep, ohne Messung |
| `--dump-commands [DATEI]` | Befehlsliste des Geraets (`SYST:HELP:HEAD?`) speichern |
| `--list-resources` | alle VISA-Ressourcen auflisten, die das Backend sieht |
| `-o, --output` | ueberschreibt `output.directory` |
| `--resource` | ueberschreibt die VISA-Adresse |
| `--dry-run` | simuliertes Geraet statt Hardware |
| `--no-prompt` | THRU-Hinweis ueberspringen |
| `--no-gui` | THRU-Hinweis in der Konsole statt als Fenster |
| `--no-plot` | nur CSV, keine Plots |

## Verbindungsprobleme

```bash
python run_s12.py --check            # IDN-Abfrage + Portscan 5025 / 111 / 4880
python run_s12.py --list-resources   # was sieht das VISA-Backend ueberhaupt?
```

`--probe` prueft nach **jedem einzelnen** Kommando die SCPI-Fehlerwarteschlange
(`SYST:ERR?`). Ein vom Geraet abgelehnter Befehl loest ueber VISA keine
Exception aus - ohne diese Abfrage liefe eine Messung mit falschen
Einstellungen stillschweigend weiter.

Zwingend sind nur Frequenzbereich und `INIT:CONT OFF`; wird eines davon
abgelehnt, bricht das Skript ab. Alle anderen Kommandos sind "best effort":
eine Ablehnung wird als `Abgelehnt: ...` gemeldet und die Messung laeuft weiter.
Fuer den Trace-Modus werden mehrere Schreibweisen der Reihe nach probiert, bis
eine akzeptiert wird.

Geraetespezifisches, das beim FPC1500 (V1.70) aufgefallen ist:

* `SWE:POIN?` wird nicht beantwortet (Timeout) - daher der Testsweep.
* `TRAC1:MODE WRIT` kennt das Geraet nicht; `DISP:TRAC1:MODE WRIT` geht.
* `OUTP:STAT` und `SOUR:POW` kennt das Geraet nicht - der FPC nennt den
  Tracking-Generator "Signal Source". `SYST:HELP:HEAD?` (via `--dump-commands`)
  listet `:SOURce:TG[:STATe]`, `:SOURce:TG:POWer`, `:SOURce:TG:FREQuency` und
  `:SOURce:TG:AUTO`. Bestaetigt funktioniert `SOUR:TG:STAT ON`.
* Der Pegelbereich der Signalquelle ist **-30 ... 0 dBm** (per
  `SOUR:TG:POW? MIN/MAX` vom Geraet gemeldet). `--probe`
  fragt ihn ueber `SOUR:TG:POW? MIN` / `? MAX` ab und warnt, wenn
  Amplitudenstufen ausserhalb liegen; die Messung bricht in dem Fall mit
  klarer Meldung ab, statt mitten im Lauf zu scheitern.
* Das Skript probiert mehrere Schreibweisen durch und meldet zu jeder
  abgelehnten den Grund im Klartext. Die gefundene merkt es sich fuer die
  ganze Messung; `--probe` zeigt sie an.
* Der zulaessige Referenzpegel haengt von der Eingangsdaempfung ab, deshalb
  wird `INP:ATT` **vor** `DISP:TRAC:Y:RLEV` gesetzt.

`VI_ERROR_RSRC_NFOUND` heisst: unter dieser Adresse meldet sich nichts.
Haeufigste Ursache ist **DHCP** - die IP des FPC1500 kann sich geaendert haben.
Aktuelle Adresse am Geraet ablesen unter `SETUP > Instrument Setup > Network`
(bzw. `SETUP > Network`), dann entweder in `config/default.json` eintragen oder
einmalig per `--resource` testen:

```bash
python run_s12.py --check --resource TCPIP0::<neue-ip>::inst0::INSTR
```

Antwortet der Portscan auf 5025, aber `::inst0::INSTR` schlaegt fehl, dann laeuft
nur der SCPI-Raw-Socket. Der funktioniert ebenfalls (Zeilenende wird automatisch
gesetzt):

```bash
python run_s12.py --check --resource TCPIP0::<ip>::5025::SOCKET
```

Damit die Adresse stabil bleibt: am FPC1500 DHCP abschalten und eine feste IP
vergeben, oder im Router eine DHCP-Reservierung auf die MAC des Geraets setzen.

## Konfiguration

```json
{
  "instrument": {
    "resource": "TCPIP0::192.168.1.187::hislip0::INSTR",
    "timeout_ms": 20000,
    "rbw_hz": 100000.0,
    "vbw_hz": 300000.0,
    "ref_level_dbm": 20.0,
    "attenuation_db": 20.0,
    "settle_s": 0.2,
    "sweep_points": 1183,
    "generator_state_command": "SOUR:TG:STAT {state}",
    "generator_level_command": "SOUR:TG:POW {level:.2f} dBm"
  },
  "frequency": { "start_hz": 80000000.0, "stop_hz": 120000000.0, "points": 401 },
  "amplitude": { "start_dbm": -30.0, "stop_dbm": 0.0, "points": 31 },
  "output": { "directory": "results", "name": "thru", "date_format": "%Y-%m-%d" },
  "measurement": { "skip_thru_prompt": false },
  "simulation": {
    "gain_db": -0.6, "p_sat_dbm": 40.0, "rolloff_db_per_ghz": 0.0,
    "points": 711, "noise_floor_dbm": -100.0
  }
}
```

Die Voreinstellung misst **80 - 120 MHz in 0.1-MHz-Schritten** (401 Punkte) bei
**-30 bis 0 dBm in 31 Stufen** (1 dB) - also 12431 Messpunkte aus 31 Sweeps.
Die -30 dBm sind das Minimum der Signalquelle des FPC1500, nicht frei gewaehlt.

Der Abschnitt `simulation` beschreibt, was `--dry-run` vorgaukelt. Voreingestellt
ist der **THRU-Fall**: beide Kalibrierkabel direkt verbunden, kein Verstaerker -
flacher Verlauf bei -0.6 dB Kabeldaempfung, keine Kompression. Fuer einen
Trockenlauf *mit* Verstaerkermodell z.B. `gain_db: 20`, `p_sat_dbm: 10`.

* `rbw_hz`, `vbw_hz`, `attenuation_db` duerfen `null` sein -> Automatik am Geraet.
* `generator_state_command` / `generator_level_command`: feste Kommandos fuer
  die Signalquelle, falls das Durchprobieren danebengreift. Platzhalter sind
  `{state}` (ON/OFF) und `{level}`, z.B.
  `"SOUR:GEN:POW {level:.2f} dBm"`. Mit `null` sucht das Skript selbst.
* `sweep_points`: Der FPC1500 liefert 1183 Trace-Punkte und beantwortet
  `SWE:POIN?` **nicht** (die Abfrage laeuft in einen Timeout). Mit `null`
  ermittelt das Skript die Zahl aus einem Testsweep.
* `points: 1` misst nur den Startwert.
* Unbekannte Schluessel fuehren zu einer klaren Fehlermeldung statt stiller Annahme.

**Fuer die spaetere Messung mit Verstaerker** muessen `ref_level_dbm` und
`attenuation_db` hochgesetzt werden (die Voreinstellung 10 dBm / 10 dB passt zum
THRU-Durchlauf, nicht zu einem 20-dB-Verstaerker bei 0 dBm Eingangspegel).

**Achtung Pegel:** `ref_level_dbm` und `attenuation_db` muessen zur erwarteten
Ausgangsleistung des Verstaerkers passen, sonst uebersteuert der Analyzer-Eingang.
Der FPC1500-Eingang vertraegt maximal +30 dBm (Dauerleistung) - ggf. ein
Daempfungsglied vorschalten.

## Ausgabe

```
results/2026-09-14_amp1/
├── s12_measurement.csv        # timestamp, frequency_hz, p_in_dbm, p_out_dbm, gain_db
├── config_used.json           # verwendete Parameter fuer die Nachvollziehbarkeit
├── s12_power_transfer.png     # P_out ueber P_in, eine Kurve je Frequenz
├── s12_frequency_response.png # P_out ueber Frequenz, eine Kurve je Pegel
└── s12_gain_map.png           # Gain ueber Frequenz und Pegel
```

## Struktur

| Modul | Inhalt |
|---|---|
| `config.py` | JSON laden, validieren, Frequenz-/Amplitudenraster, datiertes Ausgabeverzeichnis |
| `instrument.py` | `FPC1500` (SCPI ueber PyVISA) und `SimulatedFPC1500` fuer Trockenlaeufe |
| `dialog.py` | wegklickbarer THRU-Hinweis (Tk, Konsolen-Fallback) |
| `measurement.py` | Messschleife, CSV schreiben/lesen, Rastermatrix |
| `plotting.py` | die drei Plots |
| `cli.py` | Argumente, Gesamtablauf |

Ein Geraeteobjekt muss diese Methoden bieten: `open`, `close`, `identify`,
`configure`, `frequency_axis`, `generator`, `set_generator_level`, `sweep`,
`read_trace`. Dadurch laesst sich jedes andere Geraet einhaengen.

## Tests

```bash
python -m pytest --cov --cov-config=.coveragerc
```

148 Tests, **100 % Statement- und Branch-Coverage** (`fail_under = 100` in
`.coveragerc`). Die VISA-Schicht ist komplett gemockt, es wird keine Hardware
benoetigt.

## SCPI-Kommandos

Verwendet werden `INST:SEL SAN`, `FREQ:STAR/STOP`, `DISP:TRAC:Y:RLEV`,
`INP:ATT[:AUTO]`, `BAND[:AUTO]`, `BAND:VID[:AUTO]`, `DET RMS`, `TRAC1:MODE WRIT`,
`FORM:DATA ASC`, `INIT:CONT OFF`, `INIT:IMM`, `*OPC?`, `SWE:POIN?`,
`TRAC:DATA? TRACE1` sowie fuer den Tracking-Generator `OUTP:STAT ON|OFF` und
`SOUR:POW`. Sollte deine Firmware einen Befehl anders benennen, steht er
gebuendelt in `instrument.py`.
