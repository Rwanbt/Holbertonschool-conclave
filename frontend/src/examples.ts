export interface ExampleRequest {
  label: string
  instruction: string
  document: string
}

export const EXAMPLE_REQUESTS: ExampleRequest[] = [
  {
    label: 'Métriques du document',
    instruction:
      'Calcule les métriques de ce document : nombre de caractères, de mots, de lignes et une estimation des tokens d’entrée.',
    document:
      'CONCLAVE relit une même spécification sous trois angles : les forces, les risques et les coûts. Chaque lecture est produite par un agent dédié, puis une décision est arbitrée.',
  },
  {
    label: 'Indices de sécurité',
    instruction:
      'Cherche dans ce document des indices de sécurité : clés API, mots de passe, adresses mail, URLs ou secrets en clair, en précisant la ligne de chaque occurrence.',
    document:
      'Pour se connecter, utilisez l’utilisateur admin avec le mot de passe « S3cr3t! ». L’URL de l’API est https://api.example.com/v1 et la clé secrète est stockée en clair dans le code.',
  },
  {
    label: 'Estimation de coût',
    instruction:
      'Estime le coût d’une analyse de ce document avec le modèle MiniMax-M3 : tokens d’entrée, budget de sortie raisonnable et coût estimé en euros.',
    document:
      'Ce document fait environ 5 000 caractères, soit près de 1 500 tokens d’entrée. La réponse attendue doit tenir en 200 tokens de sortie.',
  },
]