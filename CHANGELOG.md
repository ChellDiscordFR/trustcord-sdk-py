# CHANGELOG
> docs(sdk): staff_role déprécié

## Changements notables

L'API publique ne dit plus si un compte appartient au staff de Trustcord, pour qu'aucun bot ne puisse repérer les jurés parmi les membres d'un serveur. Le rôle renvoyé par le SDK vaut donc toujours None ; il reste présent pour ne pas casser le code existant et disparaîtra dans la prochaine version majeure.

## Liste complète des changements

- client : StaffRole, Status.staff_role et lookup_status documentés comme dépréciés
- README : section des rôles du staff remplacée par la dépréciation
