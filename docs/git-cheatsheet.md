\# Git Cheatsheet (rudder-pi)



Dieses Repo: `rudder-pi/`

\- Android Studio Projekt: `rudderpi/`

\- Raspberry Python später z.B.: `raspberry/`







\## 0) Daily Workflow (Standard)

```powershell

git status

git add -A

git commit -m "Kurzbeschreibung"

git push







1\) Was hab ich geändert?

git status

git diff

git diff --staged







2\) Änderungen verwerfen (noch NICHT committed)

Alles zurück auf letzten Commit:



git restore .

Nur eine Datei:



git restore path\\to\\file







3\) Aus dem Staging wieder raus (nach git add)

git restore --staged .







4\) Letzten Commit korrigieren (Message/Author/kleine Fixes)

Message ändern:



git commit --amend

Nur Author/Committer neu setzen (z.B. falsche E-Mail):



git commit --amend --reset-author --no-edit

Wenn dieser Commit schon gepusht ist:



git push --force-with-lease







5\) “Ups, ich hab committed, aber will zurück” (noch NICHT gepusht)

Commit zurücknehmen, Änderungen bleiben als Dateien erhalten:



git reset --soft HEAD~1

Commit + Änderungen komplett wegwerfen:



git reset --hard HEAD~1







6\) “Ups, ich hab gepusht” (Team-sichere Methode)

Einen Commit rückgängig machen, OHNE History umzuschreiben:



git log --oneline --max-count=20

git revert <commit-hash>

git push

Das erzeugt einen neuen Commit, der den alten neutralisiert.







7\) Branch Basics (wenn du später Features getrennt machen willst)

Neuen Branch erstellen und wechseln:



git switch -c feature/video-reconnect

Push des Branches:



git push -u origin feature/video-reconnect

Zurück zu main:



git switch main

git pull







8\) Updates holen (wenn auf GitHub was passiert ist)

git pull







9\) Notfall: “Ich hab ein Secret committed”

Sofort Secret rotieren (Token/Passwort ändern!)



Dann History bereinigen (das ist aufwendiger als revert).

Für kleine Solo-Repos manchmal: reset/rebase + force push.

Für richtige Bereinigung: git-filter-repo / BFG.







10\) Nützliche Einstellungen (einmalig)

Identität:



git config --global user.name "Christian Arrer"

git config --global user.email "christianarrer@gmail.com"

Line endings (Windows):



git config --global core.autocrlf true



