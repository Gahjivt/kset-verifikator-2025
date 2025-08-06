# Discord Bot za Upravljanje Članstvom

Ovaj Discord bot je dizajniran za automatizaciju verifikacije korisnika i upravljanja ulogama na vašem serveru na temelju statusa članstva i sekcijske pripadnosti. Integrira se s vanjskom OAuth uslugom i PostgreSQL bazom podataka kako bi uloge korisnika bile sinkronizirane.

---

## Značajke

* **Verifikacija korisnika putem OAutha:** Članovi mogu koristiti slash naredbu `/register` za pokretanje OAuth procesa, povezujući svoj Discord račun s verificiranom e-mail adresom.

* **Automatsko dodjeljivanje uloga:** Automatski dodjeljuje Discord uloge (npr. "Plavi", "Narančasti", "Crveni") na temelju verificiranog statusa članstva korisnika preuzetog iz vanjske usluge.

* **Upravljanje sekcijskim ulogama:** Dodjeljuje specifične sekcijske uloge (npr. "Comp", "Tech", "Glazbena", "Foto" itd.) korisnicima na temelju njihove verificirane sekcijske pripadnosti.

* **Dnevne provjere statusa članstva:** Planirani zadatak se pokreće svakodnevno kako bi ponovno verificirao status članstva svih registriranih korisnika u odnosu na vanjsku uslugu i ažurira njihove uloge ako se otkriju bilo kakve promjene. Korisnici s ulogom "Crveni" automatski se preskaču iz ovih provjera.

* **Integracija s PostgreSQL bazom podataka:** Sigurno pohranjuje verificirane Discord ID-ove korisnika i njihove pridružene privatne e-mail adrese za održavanje trajnih podataka.

---

## Početak rada

Slijedite ove korake za pokretanje vašeg Discord bota za članstvo.

### Preduvjeti

Prije nego što počnete, provjerite imate li instalirano i postavljeno sljedeće:

* **Python 3.8+**: Preuzmite s [python.org](https://www.python.org/downloads/).

* **PostgreSQL poslužitelj baze podataka**: Provjerite je li PostgreSQL instanca pokrenuta i dostupna.

* **Discord Bot aplikacija i token**:

    1.  Idite na [Discord Developer Portal](https://discord.com/developers/applications).

    2.  Stvorite novu aplikaciju.

    3.  Pod "Bot" dodajte bota i kopirajte njegov token.

    4.  Omogućite `PRESENCE INTENT` i `MESSAGE CONTENT INTENT` pod odjeljkom "Privileged Gateway Intents".

* **ID Discord poslužitelja**: Možete ga dobiti omogućavanjem Developer Modea u Discord postavkama, desnim klikom na vaš poslužitelj i odabirom "Copy ID".

* **Vanjska pozadinska usluga (Backend)**: Ovaj bot se oslanja na vanjsku uslugu (u kodu nazvanu "backend") koja radi na `http://localhost:8000`. Ova usluga je ključna za:

    * Generiranje OAuth veza.

    * Rukovanje OAuth povratnim pozivom.

    * Pružanje statusa članstva korisnika i informacija o sekciji na temelju njihove e-mail adrese.

    * Bot **neće ispravno funkcionirati bez pokrenute pozadinske usluge.**

### Instalacija


1.  **Stvorite Python virtualno okruženje**

    ```bash
    python -m venv venv
    ```

2.  **Aktivirajte virtualno okruženje**:

    * **Windows**:

        ```bash
        .\venv\Scripts\activate
        ```

    * **macOS/Linux**:

        ```bash
        source venv/bin/activate
        ```

3.  **Instalirajte ovisnosti**:
    Stvorite datoteku `requirements.txt` u korijenskom direktoriju vašeg projekta sa sljedećim sadržajem:

    ```
    discord.py
    aiohttp
    psycopg2-binary
    python-dotenv
    ```

    Zatim ih instalirajte:

    ```bash
    pip install -r requirements.txt
    ```

---
