# TRCC RAVE V7.1 — LCD Thermalright qui pète les plombs sur la musique

## V7.2 : MONITORING / OVERLOAD
- Intro en 4 temps quand l'OVERLOAD démarre (6 s) :
  1. infection du monitoring : glitches qui montent, codes hex, teinte rouge,
     tête de mort subliminale, bandeau INTRUSION DETECTED ;
  2. tête de mort pixel art rouge dont la mâchoire claque sur les kicks, HACKED,
     « SYSTEM COMPROMISED » puis « ROOT ACCESS: GRANTED » ;
  3. pluie Matrix verte qui accélère, fantôme de la tête de mort, OVERLOAD qui se déchiffre ;
  4. explosion (flash, kaléidoscope), puis OVERLOAD à fond.
  Si l'OVERLOAD revient moins de 30 s après en être sorti : intro courte (2,3 s).
- OVERLOAD plus frénétique : changement de GIF tous les 4 kicks (tous les 2 en drop),
  lecture des GIFs jusqu'à 2,2x, stutter (sauts arrière) et miroir/mosaïque/kaléidoscope
  sur les beats, strobe blanc/néon alterné, mot glitché tous les 4 kicks (tous les 2 en drop),
  palette changée tous les 8 kicks, zoom et tremblement plus forts.
- Détection : seuil d'entrée 0.60. Les attaques et la saturation pèsent plus que le
  volume et la compression (un morceau doux mais masterisé fort reste en monitoring).
  Il faut 6 s de silence avant de revenir au monitoring : une pause ou un
  changement de piste ne casse plus l'ambiance.

## V7.1 : modes CALME (MONITORING) / ACTIF (OVERLOAD)
- **CALME** = l'affichage Nova V6 d'origine (températures, charges, heure). Il s'affiche :
  sans musique (4 s de silence), avec une musique douce, ou quand la musique reste calme 8 s.
- **ACTIF** = la rave. Elle se déclenche quand la « chaleur » dépasse 0.55 pendant 3 s.
  Dans ce mode, l'intensité des effets suit la chaleur : groove léger, puis rave, puis drop.
- Chaleur (barre fine sous le HUD) : densité d'attaques, platitude spectrale (saturation,
  charlestons), dynamique compressée et niveau, sur 6 s. Calibration sur des morceaux de
  référence : piano/acoustique 0.0–0.2, lo-fi 0.2, pop joyeuse/jazz 0.4–0.5, électro 0.8–1.0.
- Transitions : CALME→ACTIF = « BREACH » (inversion, déchirure, neige) ; ACTIF→CALME =
  « SIGNAL LOST » puis extinction CRT (ligne, point), rallumage CRT de l'affichage V6.
- Coupes de GIF : wipe à bords déchirés, blocs, zoom blast, neige.
- Terminal de hack par sessions (3,5 à 6,5 s) : entrée et sortie glitchées, lignes
  tapées en direct, flash blanc, fondu des vieilles lignes, côté gauche ou droit.
- Réglages en haut de `rave.py` : `ENTER_HEAT`, `ENTER_HOLD`, `LEAVE_HEAT`, `LEAVE_HOLD`,
  `SILENCE_HOLD`. Toutes les 30 s, le journal affiche la chaleur et ses composantes.
- Test hors-ligne : `python3 rave.py --analyze morceau.mp3` (chaleur toutes les 5 s + bascules)
- Rendu de test avec un fichier : `python3 rave.py --preview 20 --audio-file morceau.mp3`


Service `trcc-rave.service` (systemd --user, démarre à l'ouverture de session).

## Ce que ça fait
- Écoute **la sortie son par défaut** (moniteur PipeWire de l'AG06). Rien n'est enregistré.
- FFT en continu : basses, médiums, aigus, **détection des kicks**, BPM, **mode DROP**
  (quand l'énergie monte d'un coup par rapport aux 8 dernières secondes).
- 81 GIFs d'anime (nekos.best, SFW : dance / punch / kick / shoot / yeet / smug / run)
  **coupés sur le beat** (tous les 8 kicks, tous les 4 en DROP, et les GIFs de frappe
  sont privilégiés en DROP). La vitesse de lecture suit le BPM.
- Sur chaque kick : zoom punch, tremblement, flash strobe, parfois inversion.
- Glitch : RGB split, slices décalées, blocs corrompus/datamosh, neige, scanlines CRT.
  Les charlestons et caisses claires déclenchent aussi des glitches.
- Terminal de hack qui défile (hex, shellcode, katakana), plus vite quand ça cogne.
- Gros mots glitchés : BREACH, OVERDRIVE, 暴走, 限界突破, ブースト…
- Spectre néon segmenté 32 bandes, palette qui change toutes les 16 mesures (5 palettes).
- HUD : heure, BPM, températures CPU, GPU et 6950 XT (couleur selon la chauffe).
- Silence : « // NO SIGNAL // », glitch léger, GIFs au ralenti.

## Commandes
- `./activate.sh` : active RAVE V7 (désactive la V6)
- `./restore-v6.sh` : revient au carrousel Nova V6
- `python3 fetch_gifs.py --per 30 dance punch` : télécharge plus de GIFs
- Tes propres GIFs ou MP4 : dépose-les dans `gifs/`. Préfixe `punch-`, `kick-`, `shoot-`,
  `yeet-` ou `run-` = clip de frappe pour les drops. Le reste sert au groove.
- `python3 rave.py --preview 12` : rendu de test dans `preview/` sans toucher au LCD
- `journalctl --user -u trcc-rave -f` : logs

## Technique
Même chemin que la V6 : image 320x240 entière → rotation fil 90° (profil SCSI FBL51
de TRCC) → RGB565 big-endian → commande publique `SendFrame`. L'encodeur a été
vérifié octet par octet contre celui de la V6, lui-même validé contre l'encodeur Qt de TRCC.
L'envoi se fait dans un thread séparé (environ 40 ms par envoi). Résultat : 20 FPS
stables, environ 6 ms de rendu par image, environ 20 % d'un cœur.
Polices (OFL, Google Fonts) dans `fonts/` : Orbitron, Share Tech Mono, DotGothic16.
