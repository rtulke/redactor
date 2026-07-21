# redactor

**Version:** 1.3.1
**Author:** Robert Tulke <rt@debian.sh>
**License:** MIT

*[English version](README.md)*

Texte, Logs, READMEs und Code anonymisieren — schnell und einfach.

`sed` mit fester Regel-Liste: liest Text von stdin (oder aus Dateien), wendet alle
Regeln aus deinen Config-Dateien an und schreibt das Ergebnis nach stdout.

```bash
cat /var/log/messages | redactor                       # Log filtern
redactor --diff README.md                              # Vorschau
redactor -i --map .redactor.map README.md src/*.py     # Dateien umschreiben
redactor -r --diff .                                   # ganzen Baum vorschauen
redactor -r -i . --exclude 'tests/*'                   # Baum in-place schwärzen
redactor --check README.md                             # nur prüfen, für pre-commit
redactor --audit access.log                            # was hab ich vergessen?
redactor --unredact --map .redactor.map antwort.txt    # Map rückwärts
```

Keine Abhängigkeiten, nur Python 3 (stdlib). Kein venv, kein PyPI.

## Installation

**Debian / Ubuntu**

```bash
curl -LO https://github.com/rtulke/redactor/releases/latest/download/redactor_1.3.1_all.deb
sudo apt install ./redactor_1.3.1_all.deb
```

**RHEL / Rocky / Alma / Fedora**

```bash
curl -LO https://github.com/rtulke/redactor/releases/latest/download/redactor-1.3.1-1.noarch.rpm
sudo dnf install ./redactor-1.3.1-1.noarch.rpm
```

**macOS**

```bash
brew install rtulke/redactor/redactor
```

**Ohne Installation**

```bash
curl -LO https://github.com/rtulke/redactor/releases/latest/download/redactor-1.3.1.tar.gz
tar xzf redactor-1.3.1.tar.gz && cd redactor-1.3.1
./redactor.py --version
```

Danach die Config anlegen — redactor läuft zwar ohne, schwärzt aber nur, was du ihm sagst:

```bash
cp /usr/share/doc/redactor/redactor.conf.example ~/.config/redactor.conf
```

Die Pakete sind **architekturunabhängig** (`all` / `noarch`) — ein Artefakt für amd64,
arm64 und alles andere. Einzige Abhängigkeit ist `python3` (≥ 3.8).

Checksummen: `SHA256SUMS` am Release.

## Fertige Profile

Kommentierte Regelsätze für die Standard-Dienste, jeweils mit echten Beispiel-Logzeilen
(liegen in `profiles/`):

```bash
redactor -p webserver    /var/log/apache2/access.log
redactor -p sshd         /var/log/auth.log
redactor -p dhcpd        /var/log/syslog
redactor -p ftp          /var/log/vsftpd.log
redactor -p mail         /var/log/mail.log
redactor -p configfiles --diff .env
redactor --list-profiles
```

## Tests

```bash
./test.sh                      # oder: python3 -m unittest discover -s tests -v
./test.sh -k Phone             # nur eine Klasse
```

Lohnt sich nach jeder Regel-Änderung. Ein Redaction-Tool versagt **still**: wenn ein
Regex bricht, sieht das Log weiterhin gut aus — es stehen nur echte Daten drin. Die
interessanten Fälle in der Suite sind deshalb die negativen: was **nicht** angefasst
werden darf (Timestamps, PIDs, Byte-Counts, `roberta`, `/var/lib/home/`).

Die Pipeline fährt dieselbe Suite über Python 3.8 bis 3.13.

## Geschwindigkeit

Für Multi-GB-Logs relevant: jede Regel hat ein **Literal-Gate** — ein billiger
Substring-Test, der vorab klärt, ob die Regel überhaupt matchen *kann*. `@uri` ohne
`://`, `AKIA…` ohne `akia`, `@path` ohne `/home/` → Regex wird gar nicht erst gestartet.
Auf einer typischen Logzeile fallen so fast alle der ~25 Regeln raus, statt je einen
vollen Scan **plus String-Allokation** zu kosten.

Bewusst *nicht* gemacht: Literale zu einer grossen Alternation bündeln. Das würde die
"Regeln arbeiten auf dem Ergebnis der vorherigen"-Semantik brechen — den Preis ist die
Ersparnis nicht wert.

---

# Typische Workflows

## 1. Log kurz durchschleusen

```bash
cat /var/log/messages | redactor | less
journalctl -u nginx | redactor > ticket-anhang.txt
journalctl -u nginx --since today | redactor | mail -s "nginx-Fehler heute" support@example.com
ssh web01 'tail -n 500 /var/log/syslog' | redactor | less
docker logs app 2>&1 | redactor > app.log
kubectl logs deploy/api | redactor | gh issue create -F - -t "api crash loop"
xclip -o | redactor | xclip -i          # Zwischenablage anonymisieren (Linux)
pbpaste | redactor | pbcopy             # dasselbe auf macOS
```

## 2. Mehrere Dateien anonymisieren, die zusammengehören

**Hier ist `--map` wichtig.** Ohne Mapping-Datei fängt die Nummerierung bei jedem Aufruf
neu an: `web01` wird in der README zu `host1`, im Code zu `host3` — und dein Paket ist
in sich widersprüchlich. Mit `--map` bleibt es über alle Dateien und Läufe konsistent:

```bash
redactor --diff --map .redactor.map README.md src/*.py    # erst schauen
redactor -i    --map .redactor.map README.md src/*.py     # dann machen
```

Ein bereits existierendes `.redactor.map` wird **automatisch gefunden** (aufwärts gesucht,
wie `.redactor`) — dann brauchst du `--map` gar nicht mehr zu tippen. Angelegt wird es nur
bei explizitem `--map`, damit kein Lauf aus Versehen still anfängt, eine Map zu schreiben.

Die Map ist einfaches JSON, du kannst reinschauen und sie von Hand vorbelegen:

```json
{
  "hostname": { "web01.corp.local": "host1", "web01": "host2" },
  "ip":       { "192.168.1.50": "10.0.0.1" }
}
```

> **Achtung:** Die Map ist der Schlüssel zum Zurückrechnen. Sie gehört **nicht** ins
> Repo — steht bereits in `.gitignore`.

## 3. Prüfen statt ersetzen (pre-commit / CI)

`--check` schreibt keine Ausgabe, sondern meldet nur, was ersetzt *würde*, und liefert
Exit-Code 1 bei Treffern:

```bash
$ redactor --check README.md
redactor: 3 match(es) would be redacted
       2  @ip
       1  @secret query-param
$ echo $?
1
```

Als Git-Hook in `.git/hooks/pre-commit`:

```bash
#!/bin/bash
files=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$files" ] && exit 0
if ! redactor --check $files; then
    echo "Commit gestoppt: da stehen noch echte Daten drin."
    echo "Fix:  redactor -i --map .redactor.map $files"
    exit 1
fi
```

## 4. Zurückrechnen

Wenn dir jemand im Ticket auf `host1` antwortet, willst du wissen, welche Maschine das war:

```bash
redactor --unredact --map .redactor.map antwort.txt
```

Das dreht die Map um. **`@secret` ist davon ausgenommen** — Secrets werden hart auf
`[SECRET]` gesetzt statt pseudonymisiert, es gibt also keine Map dafür. Das ist Absicht:
ein Token soll weg sein, nicht wiederherstellbar.

---

# Config-Dateien

Werden in dieser Reihenfolge geladen, und die Regeln werden in dieser Reihenfolge
angewandt — alle Dateien werden kumuliert, spätere Regeln laufen zuletzt:

1. `/etc/redactor.conf`
2. `$XDG_CONFIG_HOME/redactor.conf` (Default: `~/.config/redactor.conf`)
3. das nächste `.redactor`, von `$PWD` aufwärts gesucht (wie `.gitignore`)
4. Profile aus `-p/--profile`
5. Dateien aus `-f/--file`
6. Regeln aus `-e/--expr`

`--no-config` überspringt 1–3. `--list-rules` zeigt, was tatsächlich geladen wurde
(inkl. Herkunft) — der schnellste Weg, um "warum greift meine Regel nicht?" zu klären.

Pro Projekt ein `.redactor` im Repo-Root ist das übliche Muster: die projektspezifischen
Namen dort, deine persönlichen (`@user`, `@hostname`) in `~/.config/redactor.conf`.

---

# Regel-Syntax

Eine Regel pro Zeile: `<suchmuster> <ersatz>`. `#` am Zeilenanfang ist ein Kommentar.
Dieselbe Syntax gilt für `-e`, also ist `-e 'robert user1'` identisch zur Config-Zeile
`robert user1`.

| Form | Beispiel | Was es tut |
|---|---|---|
| Literal | `robert user1` | Substring, überall |
| Wortgrenze | `@word robert user1` | nur als ganzes Wort |
| Klassen-Erkennung | `@ip`, `@uri`, `@secret` | erkennt eine ganze Kategorie |
| Regex | `/sess_[0-9a-f]+/ X` | wenn die drei oben nicht reichen |
| Block | `@block /BEGIN/ /END/ X` | mehrzeilig |

## 1. Literal — der Default

Der Suchtext wird **wörtlich** genommen. Kein Regex, also brauchen `.`, `@`, `+`, `(`
kein Escaping. Das ist Absicht: `foo@email.com` soll nicht versehentlich als Regex
interpretiert werden.

```
company.com             example.com
foo@email.com           redacted@example.com
"Robert Mueller"        "Max Mustermann"     # Quotes für Werte mit Leerzeichen
hunter2                                       # ohne Ersatz -> [REDACTED]
```

Literal matcht **auch mitten im Wort**:

```
Regel:   robert user1

  robert hat sich eingeloggt      ->  user1 hat sich eingeloggt      ✓
  von roberta                     ->  von user1a                     ← Teilwort!
  user robert123 failed           ->  user user1123 failed           ← Teilwort!
```

## 2. `@word` — nur ganze Wörter

```
Regel:   @word robert user1

  robert hat sich eingeloggt      ->  user1 hat sich eingeloggt      ✓
  von roberta                     ->  von roberta                    ← bleibt!
  user robert123 failed           ->  user robert123 failed          ← bleibt!
  hallo robert, wie gehts?        ->  hallo user1, wie gehts?        ✓ (Komma ist Grenze)
  (robert)                        ->  (user1)                        ✓
  robert@firma.ch                 ->  user1@firma.ch                 ✓ (@ ist Grenze)
  xrobert                         ->  xrobert                        ← bleibt!
```

"Wort" heisst: keine Buchstaben, Ziffern oder `_` direkt davor/dahinter. Satzzeichen,
Leerzeichen, `@`, `/`, Zeilenanfang und Zeilenende sind gültige Grenzen.

### Welche Form nehme ich wann?

**Im Zweifel Literal.** Die Fehlerrichtungen sind nicht gleich schlimm:

- Literal zu breit → über-redigiert (`roberta` → `user1a`). Hässlich, aber **nichts leakt**.
- `@word` zu eng → unter-redigiert (`robert123` bleibt stehen). Das ist ein **echter Leak**.

Nimm `@word`, wenn das Suchwort kurz und gängig ist und als Silbe in anderen Wörtern
vorkommt (`rob`, `admin`, `test`, `max`) — und schau danach mit `--stats` nach, ob die
Regel überhaupt noch greift.

## 3. Klassen-Erkennung

Erkennen eine ganze Kategorie und vergeben pro distinktem Wert ein **stabiles** Pseudonym.

| Regel | Wirkung |
|---|---|
| `@ip` | jede IPv4/IPv6 → `10.0.0.1`, `fd00::1` |
| `@email` | jede Mail-Adresse → `redacted1@example.com` |
| `@mac` | jede MAC → `02:00:00:00:00:01` |
| `@hostname` | eigener Hostname → `host1`. Mit Argumenten: `@hostname web01 /srv[0-9]{3}/` |
| `@user` | aktueller User → `user1`. Mit Argumenten: `@user robert /svc-.*/` |
| `@uri` | `https://git.corp/x?token=a` → `https://host1/x` |
| `@path` | `/home/alice/...` → `/home/user1/...`. Mit Argumenten: Prefix-Ersatz |
| `@sshkey` | `SHA256:47DEQ...`, `ssh-rsa AAAA...` → `sshkey1` |
| `@phone` | `+41 79 123 45 67` → `phone1` (nur international; national per Land) |
| `@secret` | API-Keys, Tokens, Hashes, `password=…` → `[SECRET]` |
| `@jwt` | JSON Web Tokens `eyJ….eyJ….sig` → `[SECRET]` |
| `@field KEY…` | Wert eines benannten Felds (JSON / `key=` / Header) → `[REDACTED]` |
| `@creditcard` | 13–19 Ziffern mit gültiger Luhn-Prüfsumme → `[CARD]` |
| `@iban` | IBANs mit gültiger mod-97-Prüfsumme → `[IBAN]` |
| `@keep` | Gegenteil: diese Werte **nie** ersetzen |

`@hostname`, `@user` und `@sshkey` sind **automatisch wortgebunden**.

### Namen oder Muster

`@hostname` und `@user` nehmen Klarnamen, `/regex/`-Muster oder beides gemischt:

```
@hostname web01 db01.corp.local /srv[0-9]{3}/
@user robert /svc-.*/
```

Ein Muster ist für eine **Flotte mit Namensschema** gedacht — dort bedeutet
Einzelauflistung, dass die nächste neu gerackte Maschine beim ersten Auftauchen im
Log leakt, und du erfährst es hinterher.

Warum nicht einfach eine Regex-Regel? Weil `/srv[0-9]{3}/ HOST` **jede** Maschine
`HOST` nennen würde. Ein Muster hier läuft weiterhin durch das Mapping:

```
Regel:   @hostname /srv[0-9]{3}/

  srv208.example.com  ->  host1.example.com
  srv113.example.com  ->  host2.example.com
  srv208.example.com  ->  host1.example.com     ← wieder die 1
```

Damit bleibt genau das erhalten, wofür man redactor statt `sed` nimmt: du siehst
weiterhin, welche Zeilen dieselbe Maschine betreffen — ohne zu wissen, welche.

Namen und Muster teilen sich **eine** Mapping-Tabelle, `@hostname web01 /srv[0-9]{3}/`
nummeriert also in einer Folge durch. Intern werden zwei Regeln daraus — ein Literal
ist sein eigenes billiges Gate, ein Regex lässt sich gar nicht gaten (siehe
[Geschwindigkeit](#geschwindigkeit)) — sichtbar ist das nur in `--list-rules`.

**Stabil heisst:** derselbe Wert wird zum selben Pseudonym.

```
Regel:   @ip

  10:03 login from 192.168.1.50   ->  10:03 login from 10.0.0.1
  10:04 login from 8.8.8.8        ->  10:04 login from 10.0.0.2
  10:07 logout   192.168.1.50     ->  10:07 logout   10.0.0.1     ← wieder die 1!
```

Das ist der Unterschied zu `sed s/ip/x/`: im redigierten Log ist noch erkennbar, dass
Zeile 1 und 3 **derselbe** Client waren — ohne zu verraten, welcher. Über mehrere
Läufe hinweg gilt das nur mit `--map`.

Die Pseudonymisierung ist **idempotent**: `redactor` zweimal über dieselbe Datei laufen
zu lassen macht aus `host1` kein `host2`.

### `@uri`

```
  https://bob:pw@git.corp.local/team/proj?token=abc#frag
    -> https://host1/team/proj
```

Behalten wird Schema, Port und Pfad; der Host wird pseudonymisiert (**dieselbe** Tabelle
wie `@hostname`, bleibt also konsistent); **userinfo, Query und Fragment fliegen raus** —
genau dort stecken Credentials und Tokens. Der Pfad bleibt, weil er meist die Information
ist, wegen der du das Log überhaupt aufgehoben hast.

Praktisch für DSNs: `postgres://app:s3cr3t@db.corp:5432/prod` → `postgres://host1:5432/prod`.

> `@uri` braucht `://`. Ein Access-Log-Request (`"GET /admin?token=abc HTTP/1.1"`) hat
> kein Schema — den Query-String erwischt `@secret`, nicht `@uri`.

### `@path`

Ohne Argumente erkennt es Home-Verzeichnisse und mappt den User-Teil über **dieselbe**
Tabelle wie `@user`:

```
  /home/alice/myproject/dev/x.py  ->  /home/user1/myproject/dev/x.py
  /Users/robert/Documents         ->  /Users/user2/Documents
  /var/lib/home/cache             ->  /var/lib/home/cache   ← kein Home-Dir, bleibt
```

Mit Argumenten ist es ein **literaler Prefix-Ersatz**:

```
@path /home/alice/myproject/    PROJECT/

  /home/alice/myproject/dev/x.py  ->  PROJECT/dev/x.py
```

> **Warum `@path` und nicht einfach eine Literal-Regel?** Weil
> `/home/alice/myproject/ PROJECT/` als *Regex* geparst würde — das Suchmuster fängt mit
> `/` an und hört mit `/` auf. Quoting hilft nicht. `@path` umgeht die Mehrdeutigkeit,
> weil das Kommando vorne steht.

### `@phone`

Der heikelste Detektor im ganzen Tool. Jede andere Klasse hat eine markante Form
(`AKIA…`, `SHA256:`, `://`) — eine Telefonnummer ist nur eine Ziffernfolge, und Logs
bestehen aus Ziffernfolgen. Deshalb ist der Default bewusst eng:

```
Regel:   @phone                        (Default: nur international)

  +41 79 123 45 67          ->  phone1
  +41791234567              ->  phone1        ← dieselbe Nummer, dasselbe Pseudonym
  0041791234567             ->  phone1
  079 123 45 67             ->  079 123 45 67 ← national, per Default aus
  1712913802                ->  1712913802    ← Timestamp, bleibt
  sshd[1234]                ->  sshd[1234]    ← PID, bleibt
  sent 1234567 bytes        ->  sent 1234567 bytes
```

Zwei Schutzmechanismen: das `+`/`00`-Präfix als Signal, und eine Ziffern-Zählung
(8–15, wie E.164) — was nicht passt, bleibt unangetastet. Zusätzlich wird auf die
**normalisierte** Nummer gemappt, damit `+41 79 123 45 67` und `+41791234567` dasselbe
Pseudonym bekommen statt zwei.

Nationale Formate sind **opt-in pro Land**:

```
@phone ch          # 079 123 45 67
@phone ch de at    # mehrere
```

Bekannt: `ch`, `de`, `at`, `us`. Anderes Land → Regex-Regel. `de`/`at` sind naturgemäss
unschärfer als `ch` (dort variiert die Länge stark) — mit `--stats` prüfen.

### `@keep` — das Gegenteil

Ohne das pseudonymisiert `@ip` auch `127.0.0.1` (sagt niemandem etwas) und `@uri` macht
aus jedem `https://github.com/foo` in deiner README ein `https://host1/foo` — und
zerstört damit das Dokument.

```
@keep 127.0.0.1 ::1 0.0.0.0 localhost
@keep github.com stackoverflow.com

  127.0.0.1                        ->  127.0.0.1                      ← bleibt
  8.8.8.8                          ->  10.0.0.1                       ← wird ersetzt
  https://github.com/x?tab=readme  ->  https://github.com/x?tab=readme ← komplett unangetastet
  https://git.corp/x?token=abc     ->  https://host1/x
```

Ein gekeepter Host lässt die **ganze** URL in Ruhe, inklusive Query — "gekeept" heisst
"das ist öffentlich", nicht "nur der Host ist ok".

`@keep` gilt **global, unabhängig von der Zeilenreihenfolge** (wird zur Match-Zeit
geprüft, nicht beim Parsen) und taucht in `--list-rules` auf — "warum wurde das nicht
ersetzt?" muss beantwortbar bleiben. Das Tool selbst hat **keine** eingebauten
Ausnahmen; die Loopback-Zeile steht aktiv in `redactor.conf.example`, damit nichts
unsichtbar passiert.

### `@secret` — das Sicherheitsnetz

Deine Config schützt nur vor dem, woran du **gedacht** hast. `@secret` fängt die
Klassiker ab. Anders als die anderen Klassen wird hier **nicht** pseudonymisiert, sondern
hart auf `[SECRET]` gesetzt — einen Token willst du weg haben, nicht korrelieren.
Deshalb ist `@secret` auch **nicht** mit `--unredact` umkehrbar.

| erkannt | Beispiel |
|---|---|
| Vendor-Tokens | `AKIA…`, `ghp_…`, `github_pat_…`, `glpat-…`, `xoxb-…`, `AIza…`, `ya29.…`, `sk_live_…`, `sk-…`, `npm_…`, `SG.…`, `key-…` (Mailgun), `dop_v1_…`, `hvs.…` (Vault), `shpat_…`, `sq0…` |
| JWTs | `eyJhbGci….eyJzdWI….xxx` |
| **Query-Parameter** | `?token=abc`, `&api_key=xyz`, `&session=…` |
| Bearer / Basic | `Authorization: Bearer …`, `Basic dXNlcjpwdw==` |
| Cookies | `Cookie: …`, `Set-Cookie: x=…`, `PHPSESSID=…` |
| Passwort-Hashes | `$6$rounds=5000$…` (shadow), `$apr1$…` (htpasswd), `$2y$…` (bcrypt) |
| Zuweisungen | `password=hunter2`, `api_key: "xyz"`, `client_secret = 'abc'` |
| SNMP | `rocommunity public` |
| Private Keys | der **ganze Block** von BEGIN bis END (mehrzeilig) |

Der Query-Parameter ist der wichtigste davon: in Apache/nginx-Logs ist `"GET /x?token=abc"`
der häufigste echte Leak, und `@uri` sieht ihn nicht (kein `://`).

Passwort-Hashes zählen als Secret: ein `$6$`-Hash aus `/etc/shadow` ist offline knackbar
— den zu veröffentlichen heisst, das Passwort mit Verzögerung zu veröffentlichen.

> **`@secret` ist bewusst übereifrig.** In Code trifft die Zuweisungs-Regel auch
> `token = get_token()` → `token = [SECRET]`. Über-redigieren ist kosmetisch, ein
> durchgerutschter Live-Key nicht. Prüf mit `--diff` oder `--stats`.

`@jwt` ist der JWT-Teil von `@secret` allein — wenn du nur Tokens strippen willst, sonst
nichts. Das fertige **`secrets`-Profil** bündelt `@secret` mit `@field` für die üblichen
Auth-Keys: `redactor -p secrets` ist der „Credentials raus, bevor ich das reinpaste"-Knopf.

### `@field KEY…` — einen Wert über seinen Key schwärzen

`@secret` matcht über die **Form** des Werts (`AKIA…`, `eyJ…`). Die heikelsten Werte haben
aber keine Form — ein Passwort, ein Session-Token, ein `X-Api-Key`. `@field` matcht
stattdessen über den **Key** und kennt die drei Schreibweisen eines Key-Value-Paars:

```bash
redactor -e '@field password authorization api_key' app.log
```

| Eingabe | Ausgabe |
|---|---|
| `{"password": "hunter2"}` | `{"password": "[REDACTED]"}` |
| `password=hunter2` / `?api_key=abc&x=1` | `password=[REDACTED]` / `?api_key=[REDACTED]&x=1` |
| `Authorization: Bearer eyJ… x` | `Authorization: [REDACTED]` |

Die Header-Form läuft bis zum Zeilenende, ein Wert mit Leerzeichen (`Bearer x`) wird also
ganz erwischt — genau das, was `@secret`s feste `assignment`-Regel nicht kann.
Case-insensitiv auf dem Key, einweg (nicht per `--unredact` zurückholbar).

### `@creditcard` / `@iban` — prüfsummen-gated PII

Beide sind reine Ziffern, das Fehltreffer-Minenfeld von `@phone` — deshalb ist jede über
eine **Prüfsumme** gated. `@creditcard` (13–19 Ziffern, mit Leerzeichen oder Bindestrich)
feuert nur bei gültiger **Luhn**-Summe; eine 16-stellige ID, die keine Karte ist, bleibt
unangetastet. `@iban` prüft **mod-97**. Feste Marker `[CARD]` / `[IBAN]`, einweg.

## 4. Regex

Muster **in Slashes wrappen**. `\1`-Backrefs funktionieren, `(?i)` macht case-insensitive.

```
/sess_[0-9a-f]{32}/             SESSION_REDACTED
/(password=)\S+/                "\1REDACTED"      # -> password=REDACTED
/(?i)geheim/                    X                 # trifft auch GEHEIM, Geheim
```

## 5. `@block` — mehrzeilig

```
@block /-----BEGIN CERTIFICATE-----/ /-----END CERTIFICATE-----/ "[CERT]"
```

Alles vom Start- bis zum End-Marker wird durch **einen** Ersatz-Text getauscht. Bewusst
zeilenbasiert implementiert (Zustandsautomat statt DOTALL-Regex über die ganze Datei),
damit Streaming bei grossen Logs funktioniert. Endet die Datei innerhalb eines Blocks,
bleibt der Rest redigiert — im Zweifel zu viel.

`@secret` bringt so einen Block für Private Keys schon mit.

---

# Optionen

| Option | Zweck |
|---|---|
| `-e RULE` | Regel direkt angeben (mehrfach) |
| `-f PATH` | zusätzliche Regeldatei laden (mehrfach) |
| `-p NAME`, `--profile` | fertigen Regelsatz laden (mehrfach) |
| `-a`, `--ask` | jede Ersetzung einzeln bestätigen |
| `-b`, `--blank` | jeden Treffer durch gleich viele Leerzeichen ersetzen |
| `-k`, `--keep-length` | Ersatz mit Leerzeichen auf Originallänge auffüllen |
| `-i`, `--in-place` | Dateien direkt umschreiben |
| `-r`, `--recursive` | in Verzeichnis-Argumente absteigen (überspringt `.git`, Symlinks, Binärdateien) |
| `--exclude GLOB` | beim Rekursieren Dateien/Verzeichnisse mit GLOB überspringen (wiederholbar) |
| `--include GLOB` | beim Rekursieren nur Dateien mit GLOB nehmen (wiederholbar) |
| `-d`, `--diff` | Unified Diff der Änderungen, keine Ausgabe |
| `--color WHEN` | `auto` (Default) / `always` / `never` — färbt den Diff und markiert die geänderte Stelle |
| `-c`, `--check` | nur prüfen, Exit 1 bei Treffern |
| `-A`, `--audit` | melden, was verdächtig aussieht und keine Regel hat |
| `-AA`, `--audit-all` | dasselbe, aber jeden Fund vollständig auflisten (keine Kappung) |
| `--strict` | mit `--audit`: Exit 1 bei jedem Fund (Commit / CI abriegeln) |
| `--entropy` | mit `--audit`: zusätzlich hoch-entropische Token-Strings melden |
| `-m PATH`, `--map` | Mapping persistent halten |
| `-u`, `--unredact` | Map rückwärts anwenden |
| `-s`, `--stats` | Trefferzahl pro Regel nach stderr |
| `-l`, `--list-rules` | geladene Regeln + Herkunft |
| `--list-profiles` | verfügbare Profile anzeigen |
| `-n`, `--no-config` | nur `-e`/`-f` verwenden |
| `-V`, `--version` | Version |

`-i`, `-d`, `-c` und `-A` schliessen sich gegenseitig aus. `-V` ist gross, damit `-v`
für ein späteres `--verbose` frei bleibt; `-A` ist gross, weil `-a` schon `--ask` ist.

## `-d` / `--diff` und `--color`

```bash
redactor -d access.log            # Farbe, wenn es ein Terminal ist
redactor -d access.log | less -R  # -R lässt less die Escapes durch
redactor -d --color=never access.log > review.patch
```

Ganze Zeilen rot/grün nützen bei einem Log wenig: in einer 200-Zeichen-Zeile, in der
sich eine IP bewegt hat, siehst du *dass* sich was geändert hat, aber nicht *was*.
Deshalb werden die `-`/`+`-Paare ein zweites Mal zeichenweise verglichen, und nur die
Zeichen, die sich wirklich unterscheiden, werden invertiert:

```
--- access.log
+++ access.log (redacted)
@@ -1,2 +1,2 @@
-Jul 16 13:56:56 srv208.example.com sshd[2764440]: Accepted publickey for robert from 10.0.10.113
+Jul 16 13:56:56 host1.example.com sshd[2764440]: Accepted publickey for user1 from 10.0.0.1
                 ^^^^^^^^^^^^^^^^^^                                       ^^^^^^      ^^^^^^^^^^^
                 invertiert, der Rest der Zeile ist schlicht rot / grün
```

`auto` färbt nur, wenn stdout ein Terminal ist — eine Umleitung in eine Datei oder eine
Pipe nach `patch` bleibt also sauber. `NO_COLOR` (gesetzt und nicht leer) und `TERM=dumb`
schalten `auto` ab; ein explizites `--color=always` schlägt beide — eine Umgebungsvariable
soll nicht überstimmen, was du auf der Kommandozeile getippt hast.

Die Zeilenzahl bleibt 1:1, ausser bei `@block`, das viele Zeilen zu einer zusammenfasst.
Dafür gibt es keine sinnvolle zeichenweise Zuordnung — solche Hunks bekommen schlichte
Zeilenfarben.

## `-A` / `--audit` — "was hab ich vergessen?"

Das adressiert die eigentliche Schwäche des ganzen Ansatzes: **redactor schützt nur vor
dem, woran du gedacht hast.** `@secret` ist ein Netz für bekannte Formen — `--audit` ist
eins für unbekannte.

```
$ redactor -p webserver --audit access.log
redactor: audit - 6 value(s) no rule touched, in 3 categor(ies)

  internal-host  (2 distinct, 41 hit(s))
    db.corp.local                     28x  first at access.log:12
    mail.corp.local                   13x  first at access.log:88

  long-digits  (3 distinct, 3 hit(s))
    41791234567                        1x  first at access.log:5

  base64-blob  (1 distinct, 1 hit(s))
    dXNlcjpwYXNzd29yZA==               1x  first at access.log:8

  Not necessarily leaks - audit only asks whether you meant to keep
  these. Add a rule for the real ones, @keep the intentional ones.
```

Drei Design-Entscheide, die es brauchbar machen:

- Es läuft über die **Ausgabe**, nicht die Eingabe — was schon geschwärzt ist, ist kein Fund.
- Es ignoriert **unsere eigenen Pseudonyme**. `10.0.0.1` sieht exakt aus wie eine IP; ohne
  diesen Filter würde `--audit` jede erfolgreiche Ersetzung als Verdacht melden.
- Es ignoriert alles, was `@keep` verschont hat — das war ja eine Entscheidung.

Es **redigiert nichts** und schreibt keine Ausgabe, Exit-Code ist immer 0. `--audit` fragt,
es entscheidet nicht — deshalb ist es bewusst geschwätzig: ein übersehener Wert ist ein
Leak, ein Fehltreffer ist eine Zeile Rauschen.

Der Report ist bewusst eine Zusammenfassung: jede Kategorie zeigt ihre fünf lautesten Werte
und kappt alles über 60 Zeichen, damit ein lautes Log überschaubar bleibt. Wenn `-A` dir
sagt, dass eine Kategorie den vollen Blick wert ist, hebt `-AA` (oder `--audit-all`) beide
Grenzen auf und druckt jeden Wert in voller Länge:

```bash
redactor -p webserver --audit access.log      # überfliegen: Top 5 pro Kategorie
redactor -p webserver --audit-all access.log  # die ganze Liste, ungekürzt
```

Gesucht wird nach: internen Hostnamen (`.local`, `.corp`, `.lan`, …), Domains, E-Mails,
IPs, MACs, URL-Hosts, Home-Pfaden, SSH-Keys, langen Ziffernfolgen (9+), Hex-Blobs (32+),
Base64-Blobs (24+) und `-----BEGIN`-Markern.

Zwei Modifier machen aus dem Reden mehr:

```bash
redactor -A --strict  file    # Exit 1 bei jedem Fund — Commit / CI abriegeln
redactor -A --entropy file    # zusätzlich hoch-entropische Token-Strings melden
```

`--strict` macht Audit zum Tor für die **unbekannten** Formen, wie `--check` es für die
bekannten ist. `--entropy` ergänzt das Netz für Credentials, deren Form kein Muster kennt —
ein zufälliger API-Key wird allein wegen seiner Zufälligkeit gemeldet. Konservativ (ein
camelCase-Bezeichner bleibt still) und per Default aus, weil es die lauteste Prüfung ist.

## `-p` / `--profile`

```bash
redactor -p webserver access.log
redactor -p sshd -p configfiles /var/log/auth.log
redactor --list-profiles
```

Gesucht wird in dieser Reihenfolge:

1. `$XDG_CONFIG_HOME/redactor/profiles/<name>.conf`
2. `/usr/share/redactor/profiles/<name>.conf`
3. `profiles/<name>.conf` neben dem Skript (damit ein Git-Checkout ohne Install läuft)

Eigene Profile also einfach nach `~/.config/redactor/profiles/` legen. Profile werden
**vor** `-f` geladen, deine eigenen Regeldateien können sie also ergänzen.

## `-a` / `--ask`

```
--- /var/log/messages:42   [@ip]
  - Apr 12 10:03 sshd: accepted from 192.168.1.50 port 22
  + Apr 12 10:03 sshd: accepted from 10.0.0.1 port 22
  '192.168.1.50'  ->  '10.0.0.1'
  replace? [y/n/a]
```

`y` oder Enter = ja, `n` = nein, `a` = alle restlichen ohne Nachfrage.

Der Dialog läuft über `/dev/tty`, **nicht** über stdin/stdout — deshalb funktioniert er
auch mitten in einer Pipe, wo stdin die Daten sind und stdout das Ergebnis:

```bash
cat /var/log/messages | redactor -a > clean.log    # Prompts erscheinen trotzdem
```

Ohne Terminal (Cronjob) bricht `-a` mit klarer Fehlermeldung ab, statt still alles zu
ersetzen. Blöcke werden einmal als Ganzes gefragt.

## `-b` / `--blank` und `-k` / `--keep-length`

```
Regel:   @ip

  normal:         10:03 from 192.168.1.50 port 22
  -b:             10:03 from              port 22
  -k:             10:03 from 10.0.0.1     port 22
```

`-b` wirft die konfigurierten Ersetzungen weg und macht **gleich viele Leerzeichen** —
gleich lang, nicht leer, damit Spalten und Einrückung stehen bleiben.
`-k` behält das Pseudonym und füllt nur auf die Originallänge auf.

## `-i` / `--in-place`

Braucht Dateiargumente (eine Pipe kann man nicht in-place ändern). Schreibt über
Temp-Datei + `os.replace`, also atomar — bei einem Absturz gibt es keine halb-redigierte
Datei. Dateirechte bleiben, Dateien ohne Treffer werden nicht angefasst (mtime bleibt).

> `-i` überschreibt dein Original. Beim ersten Mal `--diff` davor.

## `-r` / `--recursive` — ein ganzer Baum auf einmal

`-r` macht aus einem Verzeichnis-Argument die Liste der Textdateien darunter und übergibt
sie dann an den Modus, den du gewählt hast. Es ist *reine Datei-Expansion*: `-r -i`
schreibt den Baum um, `-r -d` zeigt ihn als einen grossen Diff, `-r -c` lässt die CI
scheitern, wenn irgendwo im Baum noch etwas leakt, `-r -A` auditiert das Ganze. Ein
Verzeichnis ohne `-r` ist ein Fehler — genauso wie `grep` sich weigert.

```bash
redactor -r -d -m .redactor.map .                 # das ganze Projekt vorschauen
redactor -r -i -m .redactor.map . --exclude 'tests/*'   # dann umschreiben
```

Der Lauf fasst bewusst nie an:

- **Versionsverwaltungs-Metadaten** (`.git`, `.hg`, `.svn`, …) — sie umzuschreiben zerstört das Repo;
- **Symlinks** — eine Redaktion könnte sonst aus dem Baum ausbrechen oder eine geteilte Datei treffen;
- **Binärdateien** — alles mit einem NUL-Byte in den ersten 8 kB (Bilder, Objekte, Archive);
- **redactors eigene `.redactor` und `.redactor.map`** — die Config enthält deine Rohgeheimnisse
  und die Map ist der Schlüssel zum Zurückdrehen; beides zu schwärzen wäre selbstzerstörerisch.

`--exclude GLOB` schliesst mehr aus (gematcht gegen den Dateinamen *oder* den Pfad relativ zum
Verzeichnis, also funktionieren sowohl `--exclude '*.min.js'` als auch `--exclude 'vendor/*'`;
ein passendes Verzeichnis wird gar nicht erst betreten). `--include GLOB` engt auf nur passende
Dateien ein; `--exclude` schlägt `--include`. Beide wiederholbar. Übersprungenes wird auf
stderr zusammengefasst.

Eine **explizit genannte** Datei wird immer verarbeitet — die Binär-/Symlink-Wächter gelten nur
für Dateien, die beim Absteigen gefunden werden, nie für eine, die du namentlich angibst.

> `-r -i` ist ein grosser Hammer über einem Quellbaum: dein komplettes Regelwerk läuft auf
> *jeder* Datei, ein breites Literal wie `@user robert` schreibt das Wort also überall um. Erst
> `-r -d` oder `-r -c`, und `--exclude` für Doku und Fixtures. Nur versionierte Dateien?
> `git ls-files -z | xargs -0 redactor -i` komponiert mit dem bestehenden In-place-Modus.

---

# Bekannte Kanten

- **Reihenfolge zählt.** `foo bar` gefolgt von `bar baz` macht aus `foo` am Ende `baz`.
- **FQDN und Kurzname** bekommen unterschiedliche Pseudonyme (`web01.corp.local` → `host1`,
  `web01` → `host2`), da es verschiedene Strings sind.
- **Ein in Slashes gewrappter Suchtext ist immer ein Regex.** Für Pfade `@path` nehmen.
- **`--ask` zeigt pro Match eine Vorschau**, die nur die aktuelle Regel berücksichtigt.
- **Zeilenweise**, ausser bei `@block`. Muster über Zeilenumbrüche brauchen `@block`.
- **`@secret` ist einweg** und mit `--unredact` nicht umkehrbar.
- **`--map` ist der Schlüssel zum Zurückrechnen.** Nicht ins Repo committen.
- **DHCP-Client-Hostnamen** (`roberts-laptop`) kann redactor nicht automatisch erkennen —
  beliebiger Freitext ohne Form. Siehe `profiles/dhcpd.conf`.

# Entwicklung

```
├── redactor.py               das Tool (stdlib only)
├── profiles/                 fertige Regelsätze
├── man/redactor.1            Man-Page
├── completion/               Bash-Completion
├── tests/                    python -m unittest discover -s tests
├── packaging/
│   ├── nfpm.yaml             deb + rpm
│   └── homebrew/redactor.rb  Master-Copy für den Tap
├── scripts/release.sh        Release-Automatik
├── ruff.toml                 Lint-Config (bewusst eng, Begründung steht drin)
├── docs/RELEASING.md         wie ein Release läuft und warum es kein -bin gibt
└── .github/workflows/        ci.yml (push/PR) + release.yml (Tag)
```

Release: siehe [docs/RELEASING.md](docs/RELEASING.md).

Die Suite bewacht die Sync-Punkte, die still verrotten: die `__version__`-Zeile, die
die Paketierung greppt, die Version in der Man-Page, die `@builtin`-Liste, die die
Completion hartkodiert, und dass nichts ausserhalb der Standardbibliothek importiert wird.

## Linting

```bash
ruff check .
```

Warum der Regelsatz eng ist, steht in `ruff.toml`. ruff ist ein **reines
Dev-Werkzeug** — es wird nie eine Laufzeit-Abhängigkeit, und `./test.sh` braucht es
nicht.

Die Installation ist auf Ubuntu 24.04 / Debian 12+ der einzige Stolperstein beim
Dev-Setup: die markieren das System-Python als *externally managed* (PEP 668),
deshalb scheitert `pip install ruff` mit `error: externally-managed-environment` —
und in apt gibt es ruff nicht. `pipx` ist genau dafür da:

```bash
sudo apt install pipx && pipx ensurepath
pipx install ruff
```

Ohne pipx: ein wegwerfbares venv (`python3 -m venv /tmp/rv && /tmp/rv/bin/pip
install ruff`) oder das Standalone-Binary von <https://astral.sh/ruff/install.sh>.

Die CI ist davon nicht betroffen — `actions/setup-python` liefert ein Python, das
nicht externally managed ist, dort läuft `pip install ruff` normal.

# Lizenz

MIT — siehe [LICENSE](LICENSE).
