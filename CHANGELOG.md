# 4.0.0 — anteprima

- Motore di base riscritto con sorgenti leggibili e moduli separati.
- Uscita temporanea, verifica SHA-256 e sostituzione atomica della ISO.
- Configurazioni validate prima di modificare l'interfaccia; password escluse.
- Le edizioni importate richiedono una nuova lettura della sorgente.
- Rilevamento di ISO cambiate durante lettura, copia e creazione.
- Cartelle di lavoro esistenti preservate, pulizia limitata alla lavorazione.
- REG confinati agli hive offline, con rilevamento del ControlSet reale.
- Gli errori nelle rimozioni app e Recall interrompono la creazione.
- Controllo StartOverride dei servizi prima della modifica.
- Mutex Windows per separare le lavorazioni concorrenti.
- Profilo Privacy, ripristino delle ultime 10 configurazioni, diagnostica.
- Supporto ai percorsi con spazi e controlli sulle dipendenze locali dei driver.
- Defender preservato; la vecchia rimozione fisica non è supportata.
- 44 test automatizzati, inclusa GUI e orchestrazione Windows simulata.
- Ricetta di build e workflow Windows; EXE di anteprima con runtime dell'allegato.
