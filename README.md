# Claude Usage for Plasma 6

Un plasmoïde KDE Plasma 6 qui affiche les limites de votre abonnement Claude Pro ou Max : le pourcentage restant sur la fenêtre de 5 heures, sur la fenêtre de 7 jours et leurs heures de remise à zéro.

Les données proviennent du champ documenté `rate_limits` que Claude Code transmet à sa [status line](https://code.claude.com/docs/en/statusline). Le widget ne lit jamais `~/.claude/.credentials.json` et n'appelle aucune API : la status line enregistre un instantané local que le widget relit chaque minute.

## Prérequis

- KDE Plasma 6 avec les modules `plasma5support` et `org.kde.quickcharts`
- Python 3
- Claude Code 2.1.211 ou plus récent, connecté avec un abonnement Claude Pro ou Max

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

## Brancher la status line Claude Code

Ajoutez dans `~/.claude/settings.json` :

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.local/share/plasma/plasmoids/com.elie.claudeusage/contents/code/claude_usage.py record"
  }
}
```

La status line affiche alors `Claude 5h 77% · 7j 59% restant`. Si vous avez déjà une status line, passez-la après `--` : le helper enregistre les limites puis lui transmet le même JSON et affiche sa sortie. Si elle échoue, le helper affiche son propre résumé.

```json
"command": "python3 ~/.local/share/plasma/plasmoids/com.elie.claudeusage/contents/code/claude_usage.py record -- ~/.claude/statusline.sh"
```

Claude Code ne fournit `rate_limits` qu'après la première réponse de l'API dans une session. Le widget affiche donc les dernières valeurs connues avec l'heure de l'observation la moins récente (« Données du 14:32 ») ; l'utilisation faite sur claude.ai entre deux sessions Claude Code n'y apparaît qu'à la session suivante. Une fenêtre dont l'heure de remise à zéro est passée est affichée à 100 %.

Chaque session Claude Code relance sa status line avec les limites de sa dernière réponse, même lorsqu'elle est inactive. Le JSON ne contenant pas d'horodatage, le helper suit chaque session (`session_id`). Une nouvelle réponse de l'API, repérée par un coût cumulé de la session (`cost.total_cost_usd`) jamais vu pour cette session, est datée par l'heure de modification du transcript (`transcript_path`, dont seules les métadonnées sont lues). Un coût déjà vu, par exemple celui d'un autre terminal ouvert sur la même session, est ignoré, et une session sans réponse (coût nul, par exemple juste après `/clear`) n'est pas enregistrée. Si une politique `modelPricing` fixe tous les tarifs à 0, le coût reste nul et le widget ne reçoit aucune donnée. Le widget affiche, pour chaque fenêtre, l'observation la plus récente. Une session est oubliée une semaine après la remise à zéro de toutes ses fenêtres.

L'instantané est stocké dans `~/.cache/claude-usage-plasmoid/rate_limits.json`. Il ne contient que les identifiants de session, leurs coûts cumulés déjà vus, les pourcentages, les heures de remise à zéro et l'heure d'observation.

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

Retirez aussi l'entrée `statusLine` de `~/.claude/settings.json`.
