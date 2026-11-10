# CHANGELOG
> feat(sdk): webhook de mise à jour, niveaux

## Changements notables

Un bot qui gardait en mémoire les réponses du registre n'apprenait jamais qu'une inscription avait été levée ou que son niveau avait changé. Trustcord peut maintenant le prévenir par un webhook, et le module trustcord.webhook vérifie que la notification vient bien de Trustcord et retrouve, parmi les comptes que le bot connaît, ceux à consulter de nouveau. La notification ne contient jamais d'identifiant en clair.

La documentation montrait un bot qui bannissait automatiquement tout compte inscrit au niveau élevé, ce qui expose son développeur à l'interdiction des décisions entièrement automatisées. L'exemple prévient maintenant un modérateur, qui décide lui-même.

La documentation décrivait aussi le niveau élevé comme des faits peut-être pénaux. Elle reprend maintenant les définitions du site : un comportement déplacé, un manquement grave ou répété aux règles de Discord, une menace contre la vie ou la sécurité d'une personne. Elle rappelle enfin que consulter le registre doit être mentionné dans la politique de confidentialité du bot, même sans sanction.

## Liste complète des changements

- README : exemple on_member_join avec alerte modérateur au lieu de member.ban, mise en garde article 22
- README : définitions des niveaux alignées sur le site, information générale due dès la consultation
- webhook : verify (signature, fenêtre de 300 secondes), account_hash, changed
- README : section webhook de mise à jour
- tests : test_webhook
