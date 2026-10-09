# 4.0.5 — pulizia finale e stato della creazione

- La mancata pulizia dei temporanei dopo la creazione e verifica della ISO viene segnalata come avviso, con il percorso residuo e lo stato «ISO creata con avvisi».
- Ritentata la rimozione dei file in sola lettura, esclusivamente nella cartella appartenente alla lavorazione e senza seguire collegamenti o junction.
- Conservati i controlli su immagini montate, hive e proprietà della cartella.
- Quattro test aggiuntivi su pulizia negata, proprietà cambiata, file in sola lettura e stato GUI: 68 test totali.

# 4.0.4 — scansione componenti più rapida

- Eliminata l'esportazione per ISO con install.wim: l'edizione selezionata viene montata direttamente in sola lettura.
- Conversione temporanea mantenuta solo per install.esd, con indice corretto dopo l'esportazione.
- Stato della scansione distinto per feature, capabilities e pacchetti.
- Quattro test PowerShell con comandi Windows simulati verificano WIM, ESD, annullamento e pulizia dopo un errore: 64 test totali.

# 4.0.3 — montaggio immagini e percorsi Windows

- Corretti i percorsi con separatori misti restituiti dal selettore di cartelle, che DISM rifiutava durante Mount-Image con errore 87 (estensione dell'immagine non rilevata).
- Normalizzazione dei soli argomenti di percorso DISM, mantenendo gli argomenti con spazi separati correttamente.
- Ripristinato DISM di Windows: nessuna selezione automatica del DISM dell'ADK basata solo sulla versione del file.
- Log DISM dedicato per lavorazione, con dettagli nativi inclusi nel log operativo in caso di errore.
- Tre test aggiuntivi: 60 test totali, inclusi percorsi Windows e log UTF-8/UTF-16.

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
