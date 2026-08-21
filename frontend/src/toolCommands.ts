import type { ToolCommandResponse, ToolName, ToolState } from './types'

export type ToolAction = 'enable' | 'disable'

export function buildToolCommand(action: ToolAction, toolName: ToolName): string {
  if (action !== 'enable' && action !== 'disable') {
    throw new Error(`action de commande inconnue : ${String(action)}`)
  }
  return `/tools ${action} ${toolName}`
}

export function buildListCommand(): string {
  return '/tools list'
}

/**
 * Le backend renvoie toujours le catalogue complet dans `response.tools` ;
 * la réponse remplace la liste locale après tout statut 200.
 */
export function reduceToolState(
  _tools: readonly ToolState[],
  response: ToolCommandResponse,
): readonly ToolState[] {
  return response.tools
}

export const TOOL_LABELS: Record<ToolName, string> = {
  measure_current_document: 'Mesurer le document',
  find_security_indicators_in_current_document: 'Rechercher les indicateurs de sécurité',
  estimate_current_analysis_cost: 'Estimer le coût de l’analyse',
}