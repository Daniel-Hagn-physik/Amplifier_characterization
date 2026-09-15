# Amplifier_characterization

Charakterisierung von RF-Verstaerkern mit dem **Rohde & Schwarz FPC1500**:
Ausgangsleistung und Verstaerkung in Abhaengigkeit von Eingangsleistung und
Frequenz, inklusive 1-dB-Kompressionspunkt.

Das Skript rastert Frequenz und Ausgangspegel der internen Signalquelle ab,
misst pro Pegelstufe einen kompletten Sweep und legt CSV und Plots in einem
datierten Verzeichnis ab. Die Daempfung von Kabeln und externen Abschwaechern
wird ueber eine THRU-Referenzmessung herausgerechnet - gemessen, nicht
eingetragen.

---

# Anleitung

## Einmalig: Installation

Im Repo-Verzeichnis eine virtuelle Umgebung anlegen und die Pakete
installieren:

```
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Alle folgenden Befehle beginnen mit `.\.venv\Scripts\python.exe`. Im
PyCharm-Terminal reicht `python`, dort ist die Umgebung schon aktiv.

## Einmalig: Verbindung pruefen

```
.\.venv\Scripts\python.exe run_s12.py --check
```

Muss die Geraetekennung ausgeben. Tut es das nicht, siehe
[Verbindungsprobleme](#verbindungsprobleme).

## Vor jeder Messreihe: Config einstellen

Es gibt genau **eine** Config: `config/default.json`. Sie enthaelt
Erklaerungen direkt als Kommentarschluessel - alles, was mit einem
Unterstrich beginnt (`"_hinweis": "..."`), wird ignoriert und ist nur fuer
dich da.

In der Datei sind alle Stellen, die du selbst eintragen musst, mit **`===>`**
markiert. Es sind genau fuenf, und pro Verstaerker aendern sich davon meist nur
zwei:

| Marke | Eintrag | Was eintragen |
|---|---|---|
| `_A_EINTRAGEN` | `frequency` | Frequenzbereich und Schrittzahl |
| `_B_EINTRAGEN` | `amplitude` | Eingangspegelbereich. Die Signalquelle kann nur **-30 bis 0 dBm** |
| `_C_EINTRAGEN` | `measurement.external_pad_db` | Nenndaempfung des externen Abschwaechers, oder `null` |
| `_D_EINTRAGEN` | `measurement.amplifier_gain_db` | Verstaerkung laut Datenblatt in dB, grob reicht. **Pflicht** |
| `_E_EINTRAGEN` | `output.name` | Name der Messung, z.B. `amp5w` |

Alles ohne Markierung sind Geraeteeigenschaften und bleiben stehen.

**Referenzpegel und Eichleitung des Analyzers musst du nicht ausrechnen** - das
Skript macht es aus `_C` und `_D` und sagt beim Start, was dabei herauskam:

```
  Pegel automatisch   : erwartet +17.0 dBm am Analyzer (Verstaerkung +37 dB, Abschwaecher 20 dB)
                        -> Referenzpegel +20 dBm, Eichleitung 30 dB
```

Die Rechnung dahinter: hoechster Eingangspegel plus Verstaerkung minus
Abschwaecher ergibt den erwarteten Pegel; der Referenzpegel wird auf die
naechste 5-dB-Stufe darueber gesetzt, die Eichleitung auf Referenzpegel + 10
(Vorgabe des Geraets). Reicht das Geraet dafuer nicht aus, bricht es mit einer
Erklaerung ab, statt stillschweigend zu begrenzen.

### Warum die Verstaerkung Pflicht ist

Ohne `amplifier_gain_db` startet das Skript nicht, sondern sagt, was fehlt. Der
Grund ist nicht Bequemlichkeit, sondern die Referenz: eine THRU-Kalibrierung
gilt nur fuer genau die Geraeteeinstellung, mit der sie aufgenommen wurde.
Referenzpegel und Eichleitung muessen also schon vor dem ersten Sweep
feststehen und fuer THRU-Lauf und Verstaerkerlauf identisch sein. Der
Vorab-Test misst die Verstaerkung zwar nach - aber dann ist die Referenz
bereits aufgenommen, und sie nachtraeglich umzustellen wuerde sie ungueltig
machen.

Der Wert muss nur grob stimmen; er legt lediglich die Geraeteeinstellung fest.
In die Messwerte geht er nicht ein. Kennst du die Verstaerkung gar nicht, trag
eine Schaetzung ein, lies die im Vorab-Test gemessene Verstaerkung ab und
wiederhole beide Laeufe - THRU **und** Messung - mit dem korrigierten Wert.

**Nicht verwechseln:** 5 W sind die *Ausgangsleistung* (+37 dBm), nicht die
Verstaerkung. Die Verstaerkung steht als eigene Zahl im Datenblatt.

Willst du die Automatik umgehen, setze `measurement.auto_level` auf `false`
oder gib `--ref-level` und `--attenuation` auf der Kommandozeile an.

Die tatsaechliche Daempfung, die tatsaechliche Verstaerkung und die
Rauschgrenze **misst das Skript ohnehin selbst**. Der Datenblattwert dient nur
dazu, die Geraeteeinstellung vorab festzulegen - er muss nur auf ein paar dB
stimmen.

## Beispiel: 5-W-Verstaerker mit 20-dB-Abschwaecher

5 W sind +37 dBm, die Verstaerkung sei laut Datenblatt 37 dB. Einzutragen:

```json
"external_pad_db": 20.0,
"amplifier_gain_db": 37.0,
"name": "amp5w"
```

Das war alles. Frequenz- und Pegelbereich (`_A`, `_B`) bleiben stehen,
Referenzpegel und Eichleitung berechnet das Skript.

## Schritt 1: THRU-Referenz aufnehmen

**Aufbau ohne Verstaerker.** Die beiden Messkabel direkt miteinander
verbinden. Sitzt bei der spaeteren Messung ein externer Abschwaecher in der
Strecke, muss er **auch jetzt schon drin sein**:

```
Signalquelle ──Kabel 1──┬──[Abschwaecher]──Kabel 2── Analyzer-Eingang
                        │
                   (hier kommt spaeter der Verstaerker hin)
```

Dann:

```
.\.venv\Scripts\python.exe run_thru.py
```

Ein Hinweisfenster erinnert an die Verkabelung, wegklicken. Ergebnis landet in
`results/<datum>_<name>_thru/`.

**Wann wiederholen?** Wenn du Kabel oder Abschwaecher wechselst oder umsteckst,
oder wenn du Frequenzbereich, Pegelbereich, Bandbreiten, Referenzpegel oder
Eingangsdaempfung aenderst. Sonst nicht - eine Referenz gilt fuer beliebig
viele Verstaerkermessungen.

## Schritt 2: Verstaerker messen

**Verstaerker einschleifen und einschalten.** Der Abschwaecher bleibt an
derselben Stelle wie in Schritt 1:

```
Signalquelle ──Kabel 1── Verstaerker ──[Abschwaecher]──Kabel 2── Analyzer-Eingang
```

```
.\.venv\Scripts\python.exe run_s12.py
```

Die Referenz aus Schritt 1 wird automatisch gefunden. Vor der eigentlichen
Messung laufen drei Pruefungen, die alle im Klartext berichten:

1. **Rauschgrenze** - Signalquelle aus, ein Sweep.
2. **Rasterabgleich** - nur Stufen, die auch in der Referenz vorkommen.
3. **Vorab-Test** - drei Messpunkte von unten her, daraus die tatsaechliche
   Verstaerkung und der hoechste noch unbedenkliche Eingangspegel.

Ergebnis in `results/<datum>_<name>/`.

## Schritt 3: Auswerten

```
.\.venv\Scripts\python.exe analyze.py
```

Oeffnet ein Dateiauswahl-Fenster, in dem du die gewuenschte
`s12_measurement.csv` auswaehlst. Die Auswertung ist von der Messung getrennt
und laesst sich beliebig oft mit anderen Parametern wiederholen.

## Wenn etwas nicht passt

| Meldung | Bedeutung | Was tun |
|---|---|---|
| `Keine THRU-Referenz gefunden` | Schritt 1 fehlt | `run_thru.py` ausfuehren |
| `Die THRU-Referenz lief mit anderen Einstellungen` | Config seit Schritt 1 geaendert | Referenz neu aufnehmen |
| `verworfen, nur X dB ueber dem Rauschen` | Pegel zu klein fuer diese Eingangsdaempfung | normal, die Stufe entfaellt |
| `Stufen entfallen oben - gemessene Verstaerkung zu hoch` | Analyzer wuerde uebersteuert | normal, die Stufe entfaellt |
| `ABBRUCH bei P_in ...` | Notbremse, Pegel ueber `max_input_dbm` | Abschwaecher vergroessern |
| `n Punkte fehlen in der Referenz` | Frequenzraster passt nicht | Referenz mit derselben Config neu aufnehmen |

---

# Pegel einstellen

Der Analyzer muss wissen, welche Leistung ihn ungefaehr erwartet. Zwei
Einstellungen, die zusammengehoeren:

```json
"ref_level_dbm": 20.0,
"attenuation_db": 30.0
```

**Regel des Geraets** (am FPC1500 nachgemessen):

```
maximaler Referenzpegel = Eingangsdaempfung - 10 dBm
```

| Eichleitung | max. Referenzpegel | hoechster unverzerrt messbarer Pegel |
|---|---|---|
| 20 dB | +10 dBm | +10 dBm |
| 30 dB | +20 dBm | +20 dBm |
| 40 dB | +30 dBm | +30 dBm |

Setze den Referenzpegel etwas ueber die erwartete Ausgangsleistung und die
Daempfung entsprechend der Regel. Zu niedrig gewaehlt, komprimiert der
Eingangsmischer und zeigt **zu wenig** an - das sieht aus wie eine Saettigung
des Verstaerkers, die es gar nicht gibt. Das Skript warnt, sobald ein Messwert
ueber dem Referenzpegel liegt.

Zu hoch gewaehlt kostet Dynamik nach unten: mehr Daempfung hebt das
Eigenrauschen im gleichen Mass an, und die untersten Pegelstufen werden
verworfen.

## Wie viel Leistung vertraegt der Eingang?

Aus den Specifications des R&S FPC (5214.7112.22):

| Groesse | Wert |
|---|---|
| Max. Eingangsleistung, CW | **+33 dBm** (2 W) |
| Max. Eingangsleistung, < 3 s | +36 dBm (4 W) |
| Max. DC-Spannung | 50 V |

Ueber +30 dBm laesst sich nicht mehr korrekt messen, ueber +33 dBm geht das
Geraet kaputt. `max_input_dbm` steht deshalb auf **30 dBm**.

## Wahl des externen Abschwaechers

Ein 5-W-Verstaerker liefert +37 dBm und braucht zwingend einen. Welche
Daempfung?

| Abschwaecher | am Analyzer | ref_level / attenuation | Reserve zu +33 dBm |
|---|---|---|---|
| 10 dB | +27 dBm | 30 / 40 | 6 dB |
| 20 dB | +17 dBm | 20 / 30 | 16 dB |
| 30 dB | +7 dBm | 10 / 20 | 26 dB |

Der nutzbare Dynamikbereich ist in allen drei Faellen **gleich**: mehr externe
Daempfung erlaubt weniger interne, und das Eigenrauschen sinkt im selben Mass.
Mehr Daempfung kostet also nichts und bringt Sicherheitsabstand.

**Achtung Verlustleistung:** ein Abschwaecher verheizt fast die ganze
Eingangsleistung - bei 5 W also knapp 5 W. Uebliche SMA-Abschwaecher vertragen
1 bis 2 W. Leistungsangabe pruefen, sonst brennt er durch und die volle
Leistung liegt ungedaempft am Analyzer.

---

# Was das Skript misst und was nicht

| Groesse | Woher |
|---|---|
| Daempfung von Kabeln und Abschwaechern | **gemessen** (THRU-Referenz) |
| Verstaerkung des Verstaerkers | **gemessen** (Vorab-Test) |
| Rauschgrenze | **gemessen** (Sweep mit Signalquelle aus) |
| Nutzbarer Pegelbereich unten | **gemessen** (Abstand zum Rauschen je Stufe) |
| Nutzbarer Pegelbereich oben | **gemessen** (Vorab-Test) |
| Anzahl der Sweep-Punkte | **gemessen** (Testsweep) |
| `max_input_dbm`, `noise_margin_db` | Einstellung - deine Sicherheitsentscheidung |
| `external_pad_db` | nur Hinweisdialog, geht nirgends in die Daten ein |
| `amplifier_gain_db` | nur Vorgabe der Geraeteeinstellung, geht nicht in die Daten ein |

**Prinzipbedingte Grenze:** Die THRU-Referenz misst die Daempfung der
*gesamten* Strecke und kann nicht trennen, was davon vor und was hinter dem
Verstaerker liegt. Die absolute Ausgangsleistung faellt deshalb um die
Daempfung des Eingangskabels zu optimistisch aus - Zehntel-dB bei kurzen
Kabeln. Die **Verstaerkung** ist davon nicht betroffen, weil sich der Anteil
in Referenz und Messung herauskuerzt.

**Bedingung:** Zwischen Schritt 1 und 2 darf sich ausser dem Verstaerker
nichts aendern. Die Geraeteeinstellungen prueft das Skript selbst (es
vergleicht die `config_used.json` neben der Referenz), die Verkabelung kann es
nicht sehen.

---

# Ausgabedateien

```
results/2026-09-15_amp1/
├── s12_measurement.csv        # Messdaten
├── config_used.json           # verwendete Parameter
├── s12_power_transfer.png     # P_out ueber P_in, eine Kurve je Frequenz
├── s12_frequency_response.png # P_out ueber Frequenz, eine Kurve je Pegel
├── s12_gain_map.png           # 2D: Verstaerkung ueber Frequenz und Pegel
└── s12_power_map.png          # 2D: Ausgangsleistung ueber Frequenz und Pegel
```

Ein zweiter Lauf am selben Tag ueberschreibt den ersten nicht - das
Verzeichnis bekommt `_2`, `_3` angehaengt.

## Spalten der CSV

| Spalte | Bedeutung |
|---|---|
| `timestamp` | Zeitpunkt der Pegelstufe |
| `frequency_hz` | Frequenz |
| `p_in_dbm` | eingestellter Pegel der Signalquelle |
| `p_out_dbm` | **Rohwert am Analyzer-Eingang**, inklusive Kabel und Abschwaecher |
| `gain_db` | roh: `p_out_dbm - p_in_dbm`, enthaelt dieselben Verluste |
| `p_ref_dbm` | was an diesem Punkt ohne Verstaerker ankam (aus der Referenz) |
| `gain_corr_db` | `p_out_dbm - p_ref_dbm` - die **Verstaerkung des Verstaerkers** |
| `p_amp_dbm` | `p_in_dbm + gain_corr_db` - die **Leistung am Verstaerkerausgang** |

Die letzten drei Spalten stehen nur mit THRU-Referenz zur Verfuegung. Sie
kommen ohne jeden Nennwert aus: die Daempfung der Strecke steckt gemessen in
der Referenz und kuerzt sich heraus.

---

# Auswertung

```
python analyze.py                          # Dateiauswahl-Fenster
python analyze.py results/2026-09-15_amp1/s12_measurement.csv
python analyze.py --linear-range -30 -12   # Fitbereich fest vorgeben
python analyze.py --compression-db 3       # 3-dB-Kompressionspunkt
```

**Berechnet wird je Frequenz einzeln:**

* Ausgleichsgerade durch den Kleinsignalbereich. Der Fitbereich wird
  automatisch bestimmt: vom kleinsten Pegel an, solange die Verstaerkung um
  weniger als `--tolerance-db` (0.25 dB) unter ihrem Maximum liegt.
* Kleinsignalverstaerkung und ihre Welligkeit ueber die Frequenz.
* Steigung des Fits als Linearitaetspruefung - 1.0000 waere ideal.
* **1-dB-Kompressionspunkt**: der Pegel, bei dem die gemessene Kennlinie 1 dB
  unter der extrapolierten Geraden liegt, zwischen den Messpunkten
  interpoliert.
* Maximale Ausgangsleistung am obersten Pegel.
* **OIP3 - nur geschaetzt**, nicht gemessen: `OIP3 = P1dB + 9.6 dB`. Diese
  Faustregel gilt fuer eine rein kubische, gedaechtnislose Kennlinie. Reale
  Verstaerker weichen ab, oft um mehrere dB. Ein **gemessener** OIP3 braucht
  zwei Toene gleichzeitig; der FPC1500 hat nur eine Signalquelle.

**Geschrieben wird** neben die CSV (oder nach `-o`):

| Datei | Inhalt |
|---|---|
| `analysis_summary.md` | Kennwerte als Text |
| `analysis_per_frequency.csv` | Verstaerkung, Steigung, P1dB und OIP3 fuer jede Frequenz |
| `analysis_transfer.png` | Kennlinie mit Fit-Gerade und markiertem P1dB |
| `analysis_compression.png` | Verstaerkung ueber P_in mit -1-dB-Linie |
| `analysis_frequency.png` | Verstaerkung und P1dB ueber der Frequenz |
| `analysis_gain_map.png` | 2D: Verstaerkung ueber Frequenz und Eingangspegel |
| `analysis_power_map.png` | 2D: Ausgangsleistung ueber Frequenz und Eingangspegel |

Enthaelt die CSV keine THRU-Referenz, sagt die Auswertung das deutlich - die
Verstaerkung enthaelt dann noch die Kabeldaempfung.

---

# Konfiguration

Es gibt genau eine Datei: **`config/default.json`**. Sie ist selbsterklaerend -
jeder Abschnitt traegt Kommentarschluessel, die beschreiben, wofuer die Werte
da sind. Schluessel mit fuehrendem Unterstrich werden vom Skript ignoriert:

```json
"amplitude": {
  "_hinweis": "Eingangspegel. Die Signalquelle des FPC1500 kann nur -30 bis 0 dBm.",
  "start_dbm": -30.0,
  "stop_dbm": 0.0,
  "points": 31
}
```

Fuer eine andere Messung aenderst du die Werte in dieser Datei - es braucht
keine zweite. Willst du eine Einstellung nur einmal ausprobieren, geht das
ueber die Kommandozeile, ohne die Datei anzufassen:

```
python run_s12.py --freq 60e6 140e6 401 --levels -10 -10 1 --no-reference
python run_s12.py --rbw 1000000 --vbw 1000000
python run_s12.py --attenuation 40 --ref-level 30
```

## Abschnitt `instrument`

* `rbw_hz`, `vbw_hz`, `attenuation_db` duerfen `null` sein -> Automatik am
  Geraet.
* **Aufloesebandbreite nicht zu klein waehlen.** Bei 100 kHz zeigt der FPC1500
  einen symmetrischen Bogen von rund 3 dB ueber den Span, der sich mit dem Span
  dehnt - ein Tracking-Fehler zwischen Signalquelle und Analyzer, keine
  Eigenschaft des Messobjekts. Bei 300 kHz sind es 0.4 dB ueber 80 MHz Span,
  bei 1 MHz nur noch 0.13 dB. Voreingestellt sind 300 kHz als Kompromiss
  zwischen Flachheit und Frequenzaufloesung. Nachpruefen laesst sich das mit
  einem einzelnen Sweep ueber einen breiteren Span:
  `python run_s12.py --freq 60e6 140e6 401 --levels -10 -10 1 --no-reference`
  - wandert der Bogen mit der Span-Mitte, kommt er vom Geraet.
* `sweep_points`: Der FPC1500 liefert 1183 Trace-Punkte und beantwortet
  `SWE:POIN?` **nicht**. Mit `null` ermittelt das Skript die Zahl aus einem
  Testsweep.
* `generator_state_command` / `generator_level_command`: feste Kommandos fuer
  die Signalquelle. Platzhalter sind `{state}` (ON/OFF) und `{level}`. Mit
  `null` probiert das Skript mehrere Schreibweisen durch.

## Abschnitt `measurement`

* `max_input_dbm`: Notbremse. Nach jedem Sweep wird der Spitzenpegel geprueft;
  wird die Grenze ueberschritten, bricht die Messung ab, **bevor** die
  naechsthoehere Stufe gefahren wird. Die bereits gemessenen Punkte werden
  normal gespeichert und geplottet, der Exitcode ist 1.
* `noise_margin_db`: geforderter Abstand zum gemessenen Rauschen. Stufen, deren
  Spitzenwert weniger darueber liegt, werden verworfen.
* `external_pad_db`: Nenndaempfung des externen Abschwaechers. Geht **nicht** in
  die Messwerte ein - die Referenz misst die Daempfung selbst. Der Wert dient
  dem Hinweisdialog und der automatischen Pegeleinstellung.
* `amplifier_gain_db`: Verstaerkung laut Datenblatt in dB. **Pflichtangabe,
  solange `auto_level` gesetzt ist.** Daraus werden Referenzpegel und
  Eichleitung berechnet. Muss nur grob stimmen - die echte Verstaerkung misst
  der Vorab-Test, sie kann aber die schon aufgenommene Referenz nicht mehr
  aendern.
* `auto_level`: `true` berechnet Referenzpegel und Eichleitung selbst.
* `gain_probe`, `gain_probe_step_db`, `gain_probe_points`: steuern den
  Vorab-Test.
* `auto_limit_levels`: `true` laesst unbrauchbare Stufen weg, `false` misst
  trotzdem alles und meldet es nur.
* `skip_thru_prompt`: unterdrueckt den Hinweisdialog.

## Abschnitt `simulation`

Beschreibt, was `--dry-run` vorgaukelt. Voreingestellt ist der THRU-Fall: beide
Kabel direkt verbunden, flacher Verlauf bei -0.6 dB, keine Kompression. Fuer
einen Trockenlauf *mit* Verstaerkermodell z.B. `gain_db: 20`, `p_sat_dbm: 10`.

---

# Vorab-Test der Verstaerkung

Auf die Datenblattangabe muss man sich nicht verlassen. Liegt eine
THRU-Referenz vor, misst das Skript die Verstaerkung selbst: es startet beim
**kleinsten** nutzbaren Pegel - der geringstmoeglichen Leistung - und steigt in
Schritten von `gain_probe_step_db` weiter, aber nur so weit, wie die bereits
gemessene Verstaerkung es als unbedenklich ausweist.

```
  Vorab-Test (Verstaerkung messen):
    P_in  -30.00 dBm  ->  Analyzer   +5.30 dBm  ->  Verstaerkung +55.00 dB
    P_in  -27.00 dBm  ->  Analyzer   +8.30 dBm  ->  Verstaerkung +55.00 dB
    P_in  -24.00 dBm  ->  Analyzer  +11.30 dBm  ->  Verstaerkung +55.00 dB
  Gemessene Verstaerkung: +55.00 dB
  Sicher bis            :  -5.30 dBm Eingangspegel (Grenze +30.0 dBm)
  Hinweis: 6 Stufen entfallen oben (-5.0 ... +0.0 dBm)
```

Die Verstaerkung folgt als Differenz zur Referenz, die Streckendaempfung muss
also nicht bekannt sein. Die sichere Obergrenze wird linear vom letzten
Messpunkt fortgeschrieben: 1 dB mehr Eingang gibt 1 dB mehr Ausgang.
Komprimiert der Verstaerker, liegt der echte Pegel darunter - die Schranke ist
also konservativ.

---

# Alle Optionen

| Flag | Wirkung |
|---|---|
| `-c, --config` | Pfad zur JSON-Config (Standard `config/default.json`) |
| `-o, --output` | ueberschreibt `output.directory` |
| `--resource` | ueberschreibt die VISA-Adresse |
| `--freq START STOP PUNKTE` | ueberschreibt den Frequenzbereich, z.B. `--freq 60e6 140e6 401` |
| `--levels START STOP STUFEN` | ueberschreibt den Pegelbereich, z.B. `--levels -10 -10 1` |
| `--rbw` / `--vbw` | ueberschreiben die Bandbreiten (Hz) |
| `--attenuation` / `--ref-level` | ueberschreiben Eingangsdaempfung und Referenzpegel |
| `--thru` | THRU-Referenzmessung (das macht `run_thru.py`) |
| `--reference THRU_CSV` | bestimmte Referenz erzwingen statt automatisch suchen |
| `--no-reference` | ohne Kabelkorrektur messen |
| `--dry-run` | simuliertes Geraet statt Hardware |
| `--no-prompt` | Hinweisdialog ueberspringen |
| `--no-gui` | Hinweis in der Konsole statt als Fenster |
| `--no-plot` | nur CSV, keine Plots |
| `--check` | Verbindung pruefen (mit Netzwerkdiagnose) |
| `--probe` | Geraete-Selbsttest: alle SCPI-Kommandos und ein Sweep |
| `--query "SCPI?"` | SCPI-Kommandos schicken; mit `?` abfragen, ohne `?` setzen |
| `--dump-commands [DATEI]` | Befehlsliste des Geraets speichern |
| `--list-resources` | alle sichtbaren VISA-Ressourcen auflisten |

---

# Verbindungsprobleme

```
python run_s12.py --check            # IDN-Abfrage + Portscan 5025 / 111 / 4880
python run_s12.py --list-resources   # was sieht das VISA-Backend?
python run_s12.py --probe            # jedes SCPI-Kommando einzeln pruefen
```

`VI_ERROR_RSRC_NFOUND` heisst: unter dieser Adresse meldet sich nichts.
Haeufigste Ursache ist **DHCP** - die IP kann sich geaendert haben. Aktuelle
Adresse am Geraet unter `SETUP > Instrument Setup > Network` ablesen.

`--probe` prueft nach **jedem einzelnen** Kommando die SCPI-Fehlerwarteschlange
(`SYST:ERR?`). Ein abgelehnter Befehl loest ueber VISA keine Exception aus -
ohne diese Abfrage liefe eine Messung mit falschen Einstellungen still weiter.
Zwingend sind nur Frequenzbereich und `INIT:CONT OFF`; alles andere ist
"best effort" und wird bei Ablehnung nur gemeldet.

---

# Geraeteeigenheiten FPC1500 (Firmware V1.70)

* Erreichbar **nur ueber HiSLIP**: `TCPIP0::<ip>::hislip0::INSTR`. Port 5025
  (Raw Socket) ist zu, VXI-11 (`::inst0::INSTR`) scheitert.
* Die Signalquelle heisst "TG": `SOUR:TG:STAT ON|OFF`, `SOUR:TG:POW <dBm>`.
  `OUTP:STAT` und `SOUR:POW` kennt das Geraet nicht.
* Pegelbereich der Signalquelle: **-30 bis 0 dBm**.
* `SWE:POIN?` wird nicht beantwortet (Timeout). Das Geraet liefert 1183
  Trace-Punkte.
* Trace-Modus: `DISP:TRAC1:MODE WRIT` (nicht `TRAC1:MODE`).
* `INP:ATT` muss **vor** `DISP:TRAC:Y:RLEV` gesetzt werden - der zulaessige
  Referenzpegel haengt von der Daempfung ab.
* Die vollstaendige Befehlsliste liefert `SYST:HELP:HEAD?` (`--dump-commands`).
* `? MAX` liefert immer das Maximum unter den *gerade aktiven* Einstellungen -
  beim Abfragen also erst die Daempfung setzen:
  `--query "INP:ATT 40 dB; DISP:TRAC:Y:RLEV? MAX"`.

---

# Struktur

| Datei | Inhalt |
|---|---|
| `run_thru.py` | Einstiegspunkt THRU-Referenz |
| `run_s12.py` | Einstiegspunkt Messung |
| `analyze.py` | Einstiegspunkt Auswertung |
| `amplifier_characterization/config.py` | JSON laden, validieren, Raster, Ausgabeverzeichnis, Referenzsuche |
| `amplifier_characterization/instrument.py` | `FPC1500` (SCPI ueber PyVISA) und `SimulatedFPC1500` |
| `amplifier_characterization/dialog.py` | Hinweisdialog und Dateiauswahl (Tk, Konsolen-Fallback) |
| `amplifier_characterization/measurement.py` | Messschleife, Rauschgrenze, Vorab-Test, CSV, Referenzkorrektur |
| `amplifier_characterization/analysis.py` | Fit, Kompressionspunkt, Kennwerte |
| `amplifier_characterization/plotting.py` | alle Plots |
| `amplifier_characterization/cli.py` | Argumente und Ablauf der Messung |
| `amplifier_characterization/analysis_cli.py` | Argumente und Ablauf der Auswertung |

Ein Geraeteobjekt muss diese Methoden bieten: `open`, `close`, `identify`,
`configure`, `frequency_axis`, `generator`, `set_generator_level`,
`generator_limits`, `sweep`, `read_trace`, `check_errors`. Dadurch laesst sich
jedes andere Geraet einhaengen.

# Tests

```
python -m pytest --cov --cov-config=.coveragerc
```

298 Tests, **100 % Statement- und Branch-Coverage** (`fail_under = 100`). Die
VISA-Schicht ist komplett gemockt, es wird keine Hardware benoetigt.
