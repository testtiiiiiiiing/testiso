# 4.0.2 — componenti importati e diagnostica DISM

- Nomi dei vecchi JSON risolti dal catalogo reale di ciascuna edizione, preservando la lingua.
- Selezioni sconosciute o ambigue segnalate prima delle rimozioni, salvo componenti introdotti dagli aggiornamenti.
- Componenti già assenti o standard non disponibili registrati senza inviare nomi inesistenti a DISM.
- Rimozioni standard effettuate con identità complete; nessuna doppia rimozione standard/avanzata.
- DISM del Windows ADK preferito quando più recente del sistema; comando completo nei messaggi di errore.
- Undici test di regressione aggiunti: 57 test totali.

# 4.0.1 — correzione importazioni precedenti

- Le richieste legacy di rimozione Defender vengono ignorate, preservando la protezione.
- Lo stato interno precedente non blocca più la creazione della ISO.
- Due test di regressione aggiunti: 46 test totali.

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
