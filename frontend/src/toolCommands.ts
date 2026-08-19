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

export function reduceToolState(
  tools: readonly ToolState[],
  response: ToolCommandResponse,
): readonly ToolState[] {
  return tools.map((tool) =>
    tool.tool_name === response.tool_name
      ? { ...tool, enabled: response.enabled }
      : tool,
  )
}

export const TOOL_LABELS: Record<ToolName, string> = {
  measure_current_document: 'Mesurer le document',
  find_security_indicators_in_current_document: 'Trouver les indices de sécurité',
  estimate_current_analysis_cost: 'Estimer le coût',
}