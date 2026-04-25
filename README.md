# kopiarka

Agent do kopiowania profili praktyków z [Heallist](https://app.heallist.com) do prywatnej bazy SQLite. Tylko do użytku własnego.

---

## Wymagania

- Python 3.11+
- Dostęp do internetu
- Konto na Heallist

---

## Instalacja

```bash
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# lub: .venv\Scripts\activate    # Windows

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Uzupełnij .env swoimi danymi logowania
```

---

## Konfiguracja (`.env`)

```dotenv
HEALLIST_EMAIL=twoj@email.com
HEALLIST_PASSWORD=twoje-haslo
HEADLESS=true
```

---

## Uruchomienie

```bash
# Pełne pobieranie
python -m src.main

# Test na próbce 10 profili
python -m src.main --limit 10

# Tryb widoczny (przeglądarka na ekranie) — do debugowania
python -m src.main --headful

# Wymuś ponowne logowanie (usuwa zapisaną sesję)
python -m src.main --reset-session
```

### Opcje CLI

| Flaga | Opis |
|---|---|
| `--limit N` | Pobierz tylko pierwsze N profili |
| `--headful` | Pokaż okno przeglądarki |
| `--reset-session` | Usuń zapisaną sesję i zaloguj od nowa |
| `--skip-login` | Użyj zapisanego stanu sesji bez weryfikacji |

---

## Jak przeglądać bazę

Dane zapisywane są w `data/heallist.db` (plik SQLite, ignorowany przez git).

```bash
# Podgląd wszystkich praktyków
sqlite3 data/heallist.db "SELECT name, city, email FROM practitioners;"

# Eksport do CSV
sqlite3 -csv -header data/heallist.db "SELECT * FROM practitioners;" > practitioners.csv

# Wyszukiwanie
sqlite3 data/heallist.db "SELECT name, city FROM practitioners WHERE specializations LIKE '%yoga%';"

# Liczba pobranych profili
sqlite3 data/heallist.db "SELECT COUNT(*) FROM practitioners;"
```

---

## Struktura projektu

```
kopiarka/
├── .env.example          # Szablon pliku .env
├── .gitignore
├── README.md
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── main.py           # Punkt wejścia: python -m src.main
│   ├── auth.py           # Logowanie przez Playwright
│   ├── scraper.py        # Pobieranie listy i szczegółów profili
│   ├── db.py             # SQLite — inicjalizacja i zapis (UPSERT)
│   └── models.py         # Dataclass Practitioner
└── data/                 # Baza danych (w .gitignore, tworzona runtime)
    └── .gitkeep
```

---

## Schemat bazy danych

Tabela `practitioners`:

| Kolumna | Typ | Opis |
|---|---|---|
| `id` | INTEGER PK | Autonumeracja |
| `profile_url` | TEXT UNIQUE | URL profilu (klucz UPSERT) |
| `name` | TEXT | Imię i nazwisko / nazwa |
| `specializations` | TEXT | JSON array specjalizacji |
| `email` | TEXT | Adres e-mail |
| `phone` | TEXT | Numer telefonu |
| `website` | TEXT | Strona www |
| `social_links` | TEXT | JSON: linki social media |
| `city` | TEXT | Miasto |
| `country` | TEXT | Kraj |
| `address` | TEXT | Pełny adres |
| `bio` | TEXT | Opis/bio |
| `raw_json` | TEXT | Surowe dane (JSON/HTML) |
| `scraped_at` | TIMESTAMP | Data ostatniego pobrania |

---

## Uwaga prawna / etyczna

Skrypt służy **wyłącznie do prywatnego backupu danych** dostępnych na platformie Heallist po zalogowaniu własnym kontem. Należy:

- Respektować [Regulamin Heallist](https://app.heallist.com)
- Nie udostępniać ani nie rozpowszechniać pobranych danych
- Używać skryptu z umiarem (nie przeciążać serwisu)

---

## Rozwiązywanie problemów

### „Nie znaleziono żadnych profili"

Uruchom z `--headful` i sprawdź czy strona wyświetla listę praktyków. Może być konieczna aktualizacja selektorów CSS w `src/scraper.py` (miejsca oznaczone `# TODO`).

### „Logowanie nie powiodło się"

Sprawdź `.env` — czy email i hasło są poprawne. Uruchom `--headful` żeby zobaczyć formularz logowania. Ewentualnie zaktualizuj selektory formularza w `src/auth.py`.

### Sesja wygasła

```bash
python -m src.main --reset-session
```