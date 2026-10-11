# Claude Usage for Plasma 6

Un plasmoïde KDE Plasma 6 qui affiche les limites de votre abonnement Claude Pro ou Max : le pourcentage restant sur la fenêtre de 5 heures, sur la fenêtre de 7 jours, leurs heures de remise à zéro, ainsi que les resets de limites en stock et leur date limite d'utilisation.

Le widget interroge l'usage du compte connecté à Claude Code. Il compte donc l'utilisation de Claude Code, de claude.ai et des autres applications Claude, même quand Claude Code n'est pas ouvert.

## Prérequis

- KDE Plasma 6 avec les modules `plasma5support` et `org.kde.quickcharts`
- Python 3
- Claude Code, connecté avec un abonnement Claude Pro ou Max (`claude auth login`)

## Installation

Avec le paquet prêt à installer :

```bash
kpackagetool6 -t Plasma/Applet -i claude-usage.plasmoid
```

Ou directement depuis les sources de ce dossier :

```bash
kpackagetool6 -t Plasma/Applet -i package
```

Ensuite, ouvrez « Ajouter des composants graphiques » dans Plasma et recherchez **Claude Usage**.

Pour mettre à jour une installation existante :

```bash
kpackagetool6 -t Plasma/Applet -u claude-usage.plasmoid
```

## Source des données

Toutes les 5 minutes, et à l'ouverture du widget, le helper lit le jeton d'accès que Claude Code enregistre dans `~/.claude/.credentials.json` (ou `$CLAUDE_CONFIG_DIR/.credentials.json`) et l'envoie uniquement au point d'accès d'usage d'Anthropic (`https://api.anthropic.com/api/oauth/usage`). Ce point d'accès n'est pas documenté publiquement et peut changer.

Anthropic ne communique le stock de resets qu'à Claude Code : la requête se présente donc avec les en-têtes de Claude Code (`User-Agent: claude-cli/…`, `x-app: cli`). Le widget ne fait que lire ce stock et n'utilise jamais un reset ; si Anthropic cesse de le communiquer, les deux lignes disparaissent.

Le widget ne renouvelle jamais le jeton : Claude Code le fait quand il tourne. Si le jeton a expiré, le widget affiche « Session Claude expirée : ouvrez Claude Code » ; lancer Claude Code suffit à le renouveler.

Le dernier résultat est gardé dans `~/.cache/claude-usage-plasmoid/usage.json`, sans le jeton. Tous les widgets partagent ce fichier : le point d'accès est interrogé au plus une fois toutes les 30 secondes, y compris après un échec, et après un refus pour trop de requêtes le widget attend au moins 5 minutes, ou plus si Anthropic le demande. Si une mise à jour échoue (réseau, jeton expiré, trop de requêtes), le widget garde les dernières valeurs avec leur heure et affiche la raison en dessous ; une fenêtre dont l'heure de remise à zéro est passée est affichée à 100 %.

## Vérifier la source de données

```bash
python3 package/contents/code/claude_usage.py
```

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
tests/ClaudeAccent.sh
tests/CompactUsesKdePieChart.sh
tests/CompactTextContrast.sh
qml6 tests/CompactHostProbe.qml
```

## Désinstallation

```bash
kpackagetool6 -t Plasma/Applet -r com.elie.claudeusage
```
