# 4.0.9 — diagnostica delle scansioni lente

- Tempi separati per montaggio, letture, smontaggio WIM, chiusura ISO e cancellazione dei temporanei nel log operativo.
- Log DISM dedicato alla scansione; ultime righe incluse nel log esportabile prima di eliminare i temporanei.
- Il catalogo viene dichiarato pronto dopo la pulizia finale, segnalando eventuali file residui.
- Questa modifica permette di diagnosticare le attese della prima scansione; non promette una velocizzazione delle operazioni DISM.
- Nome dell’operazione attiva e durata aggiornati anche senza nuove percentuali; comando completo e durata nel log.
- Il motivo dell’interruzione viene scritto nel log prima dello scarto, con stato «Recupero dopo interruzione» distinto dalla creazione.
- 87 test automatizzati, inclusi avvio/fine di un processo reale, durata e recupero GUI e scansioni PowerShell con comandi Windows simulati.

# 4.0.8 — pacchetti già rimossi da altri componenti

- Confronto con l'inventario iniziale: un componente presente prima delle modifiche e poi assente viene registrato senza una seconda rimozione.
- Pacchetti riletti prima di ciascuna rimozione, anche dopo feature e capabilities che possono eliminarli come dipendenza.
- I nomi sconosciuti restano errori espliciti; il messaggio non presume più un'importazione JSON.
- Cinque test aggiuntivi: 84 test totali, incluse rimozioni simulate del pacchetto Internet Explorer tramite capability e di pacchetti tramite feature.

# 4.0.7 — riutilizzo catalogo e gruppi di stato in cima

- Catalogo salvato su disco e riutilizzato anche dopo la chiusura del programma, per la stessa identità e metadati ISO ed edizione.
- Riscansiona forza una lettura completa; cache non valida o non scrivibile non blocca la scansione.
- Scansioni interrotte o con ISO cambiata non vengono salvate nella cache.
- Montaggio in sola lettura con ottimizzazione Windows; nessuna esportazione aggiunta per WIM.
- Clic sul titolo Stato ISO porta in cima a turno Enabled, Installed, Disabled, Staged e gli altri gruppi, mantenendo tutte le righe e tornando all'inizio della tabella.
- Sei test aggiuntivi: 79 test totali, inclusi riavvio della cache, invalidazione, forzatura e gruppi di stato in cima.

# 4.0.6 — scelte direttamente nel catalogo componenti

- Eliminati i tre elenchi testuali sotto il catalogo: le scelte appaiono sulla riga selezionata.
- Tasti D per disattivare/rimuovere e R per annullare la scelta, con selezione multipla e pulsanti equivalenti.
- Filtro per tutti gli stati rilevati, inclusi Enabled, Disabled e Staged; stato ISO e scelta pianificata distinti.
- Conservati selezione, ordinamento e scorrimento durante le modifiche.
- JSON precedenti compatibili: richieste non ancora scansionate visibili come «Da verificare».
- Catalogo sincronizzato anche con le rimozioni dei componenti standard.
- Cinque test GUI aggiuntivi: 73 test totali.

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
