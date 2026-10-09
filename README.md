# WinSlim Studio 4.0

App desktop in italiano per personalizzare **ISO ufficiali Windows 10/11 x64**.
Ricostruita dal programma WinSlim Studio 3.1 allegato dall'utente: il motore di
base è stato riscritto in Python leggibile e l'interfaccia recuperata è stata
adattata. Il runtime non richiede pacchetti Python aggiuntivi.

## Download

[Scarica il pacchetto completo ZIP](https://github.com/testtiiiiiiiing/testiso/raw/refs/heads/main/downloads/WinSlim_Studio_4_0_preview.zip).
Estrai il pacchetto e apri `WinSlim-Studio-4/dist/WinSlim_Studio_4_0_preview.exe`.
È un EXE di anteprima: il collaudo nativo Windows e delle ISO reali rimane necessario.

## Avvio su Windows

L'eseguibile di anteprima è in `dist/WinSlim_Studio_4_0_preview.exe`.
Non richiede Python installato. Il programma richiede l'elevazione tramite UAC.
Per creare ISO occorre installare **Windows ADK → Deployment Tools**, che include
`oscdimg.exe`; si può indicarne il percorso nella pagina Sorgente.

Per avviare dai sorgenti, installare Python 3.13 x64 con Tcl/Tk e il launcher `py`,
quindi fare doppio clic su `run_windows.cmd`, oppure:

```powershell
cd C:\percorso\WinSlim-Studio
py -3.13 -m winslim
```

Scegli una ISO, premi **Leggi edizioni**, seleziona le edizioni, configura le
modifiche e controlla il **Riepilogo** prima di creare la nuova ISO. Tutte le
rimozioni sono disattivate inizialmente. Le modifiche riguardano la copia offline
dell'immagine, non il Windows attualmente in uso.

Usa una cartella di lavoro dedicata con almeno 20 GB liberi; per ISO grandi la
stima richiesta aumenta a quattro volte la dimensione della sorgente. È richiesto
anche spazio per l'uscita. Sono supportati percorsi con spazi. Sorgenti e uscita
devono stare fuori dalla sottocartella `WinSlim_work`. Se questa esiste già,
il programma la conserva e chiede di scegliere un'altra cartella di lavoro.

## Funzioni

- Lettura e selezione delle edizioni WIM/ESD, ISO avviabile BIOS/UEFI.
- Rimozione di app provisionate e componenti, catalogo in sola lettura.
- Driver INF firmati, anche in boot.wim; aggiornamenti CAB/MSU ordinabili.
- Installer EXE/MSI e piano energetico al primo accesso amministratore.
- Servizi offline con controllo di servizi essenziali, driver e dipendenze.
- Registro offline, file aggiuntivi, personalizzazione del profilo Default.
- Installazione automatica: lingua, account, accesso automatico e partizionamento
  esplicito del disco di destinazione. La password è scritta nel file
  `autounattend.xml` della ISO; viene esclusa da configurazioni JSON e report.
- Configurazioni JSON compatibili con schema 2, riepilogo e confronto prima/dopo.
- Annullamento fra comandi; DISM attivo viene lasciato terminare prima di scartare
  le immagini montate dalla lavorazione.

Le aggiunte principali sono il profilo **Privacy**, **Ripristina scelte / Ctrl+Z**
per annullare l'ultimo profilo o importazione (fino a 10), SHA-256 della sorgente
e dell'uscita, diagnostica Windows e validazione più rigorosa delle configurazioni.

La nuova ISO sostituisce l'uscita solo dopo che `oscdimg` e il calcolo SHA-256
sono terminati. Un errore o annullamento precedente conserva l'uscita esistente.
Se la ISO è stata creata e verificata, un errore nella pulizia finale dei
temporanei viene mostrato come avviso con il percorso residuo; l'operazione
resta completata. La pulizia ritenta sui file in sola lettura della lavorazione,
controllando proprietà della cartella, immagini montate e hive ancora in uso.
Le edizioni importate da un JSON devono essere rilette dalla ISO reale.
La scansione dei componenti monta direttamente `install.wim` in sola lettura,
senza esportare l'edizione. Per `install.esd` converte soltanto l'edizione
selezionata in una WIM temporanea. Il catalogo viene riutilizzato nella stessa
sessione e alla riapertura se identità, dimensione e date del file ISO ed edizione non cambiano. **Riscansiona** forza una lettura nuova. Lo stato distingue feature,
capabilities e pacchetti durante la lettura.
Cliccando **Stato ISO** porti in cima a turno Enabled, Installed, Disabled,
Staged e gli altri gruppi presenti, mantenendo tutte le righe e tornando
all’inizio della tabella. Nel catalogo puoi filtrare per nome, tipo e ogni stato presente (Enabled,
Disabled, Staged ecc.). Seleziona una o più righe e premi **D** per richiedere
la disattivazione delle feature con rimozione dei file o la rimozione di
capabilities/pacchetti; **R** annulla la richiesta. La colonna **Scelta** cambia
subito nella stessa tabella, mentre **Stato ISO** conserva lo stato rilevato.
La cache del catalogo è in `%LOCALAPPDATA%\WinSlim\Cache\Catalog`; una cache
assente o danneggiata viene ignorata. Il montaggio di scansione usa
l’ottimizzazione Windows; il tempo totale della prima lettura dipende da DISM.
Le modifiche vengono applicate durante la creazione. Le tre liste testuali
separate sono state eliminate; i JSON precedenti restano compatibili e le
richieste non ancora riscontrate appaiono nel catalogo come “Da verificare”.
I nomi dei componenti selezionati vengono verificati per ogni edizione.
Un pacchetto presente inizialmente e già eliminato da una rimozione di feature
o capabilities viene registrato come già assente, senza una seconda rimozione.
L’inventario viene aggiornato prima di rimuovere ciascun pacchetto.
Il log esportabile include i tempi di ogni passaggio della scansione e le
ultime righe del log nativo DISM; una copia completa viene salvata nella
cartella dei log dell’app, con il percorso indicato nel log operativo. Il passaggio 5 distingue smontaggio WIM,
chiusura ISO e cancellazione dei temporanei; questa diagnostica non garantisce
una riduzione della durata della prima scansione. Durante la creazione sono
visibili anche il comando attivo e la sua durata, aggiornata mentre la
percentuale rimane invariata. I nomi
abbreviati o con una vecchia versione vengono risolti solo quando il catalogo
fornisce una corrispondenza univoca, mantenendo la lingua. I nomi sconosciuti o
ambigui richiedono una nuova selezione dal catalogo; i componenti standard non
disponibili e quelli già assenti vengono registrati nel log e nel report.
I percorsi passati a DISM vengono convertiti al formato Windows, anche se il
selettore di cartelle restituisce barre `/`. Viene usato DISM di Windows dalla
cartella System32, senza selezionare automaticamente quello dell'ADK accanto a
`oscdimg`. Gli errori mostrano il comando completo, incluso il nome del
componente. Il log nativo `DISM.log` è nella cartella della lavorazione; in caso
di errore le ultime righe vengono incluse nel log operativo esportabile.
I percorsi che coincidono con sorgenti, anche tramite hard link, vengono respinti.
I file REG vengono indirizzati esclusivamente agli hive offline; il ControlSet
attivo viene rilevato dal registro dell'immagine.

**Defender viene preservato:** la rimozione fisica presente nell'originale è stata
esclusa perché non verificabile in modo affidabile fra build. Le richieste di rimozione Defender nei vecchi JSON vengono ignorate automaticamente,
senza bloccare la creazione della ISO.

## Test eseguiti e limiti

Sono passati **87 test** con Python 3.12 e 3.13 su Linux, con display virtuale
reale per Tk. Coprono le otto pagine, importazione, profili, annullamento,
protezione dei percorsi, registro, XML, output atomico, hash, dipendenze dei
driver e un flusso completo multi-edizione con risposte Windows simulate.
Quattro test eseguono lo script di scansione in PowerShell 7 con comandi
Windows simulati: WIM senza esportazione, ESD, annullamento e pulizia dopo
un errore. Richiedono `pwsh` o `powershell.exe` (oppure `WINSLIM_PWSH`).

**L'EXE e la creazione/avvio/installazione di ISO reali non sono stati eseguiti
su Windows.** Il pacchetto è una versione di anteprima, non una release
certificata. I test simulati non dimostrano la compatibilità di DISM, driver,
aggiornamenti o impostazioni con una particolare build di Windows. Non è
possibile garantire l'assenza assoluta di bug.

L'EXE di anteprima riutilizza bootloader e runtime Windows Python/Tcl del file
allegato, sostituendo interamente il codice applicativo con questi sorgenti.
`dist/WinSlim_Studio_4_0_preview.manifest.json` registra provenienza, hash e
stato del collaudo. Per un EXE con runtime nuovo, indipendente dall'allegato,
esegui `build_windows.cmd`: testa i sorgenti e compila con PyInstaller 6.16.0.
La workflow `.github/workflows/windows.yml` prepara lo stesso EXE su Windows
quando il repository viene pubblicato; non è stata avviata in questa sessione.

Prima di usare una ISO per installazioni reali occorre creare una ISO da una
sorgente nota, verificarne SHA-256, avviarla in una VM UEFI e verificare Setup,
primo accesso, app, servizi, driver e Windows Update. I bypass Windows 11 e
alcuni criteri dipendono dalla build. ISO ARM64/x86 e immagini SWM divise non
sono supportate.

## Test e sviluppo

```powershell
$env:WINSLIM_REQUIRE_GUI = '1'
py -3.13 -m unittest discover -s tests -v
```

Su Linux, con un display disponibile:

```bash
DISPLAY=:99 WINSLIM_REQUIRE_GUI=1 python3 -m unittest discover -s tests -v
DISPLAY=:99 python3 -m winslim
```

Senza display, i test GUI vengono segnalati come saltati. Imposta
`WINSLIM_REQUIRE_GUI=1` per considerarli obbligatori. L'interfaccia può essere
controllata su Linux; le operazioni Windows non vengono emulate dal programma.

`winslim/base.py` contiene il motore e i comandi Windows, `winslim/safety.py` la
validazione indipendente dalla piattaforma e `winslim/studio.py` l'interfaccia
e le integrazioni. `tools/export_test_scripts.py` esporta gli script PowerShell
per controllarli con il parser Windows senza eseguire modifiche.

## Recupero di una lavorazione interrotta

Conserva il log e la cartella di lavoro riportata dall'errore. Dal terminale
amministratore, `dism /Get-MountedImageInfo` elenca le immagini ancora montate.
Controlla che il MountDir sia proprio il `mount` o `bootmount` della tua
lavorazione prima di scartarlo con:

```powershell
dism /Unmount-Image /MountDir:"C:\tua-cartella\WinSlim_work\mount" /Discard
```

Se il log riporta un hive ancora caricato, scarica solo quello della lavorazione
indicata, per esempio `reg unload HKLM\WS_SYSTEM`. Le istanze di WinSlim usano
un mutex Windows per evitare lavorazioni contemporanee sugli stessi nomi hive.
Non eseguire una pulizia globale delle immagini DISM. Elimina la cartella della
lavorazione solo dopo aver verificato lo smontaggio; non contiene la ISO originale.
