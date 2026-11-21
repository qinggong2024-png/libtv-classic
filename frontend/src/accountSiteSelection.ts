import type { CutflowSiteChoices } from './api'

export function accountSiteSelection(choices: CutflowSiteChoices, preferred = ''): string {
  const allowed = (id: string) => choices.sites.some((site) => site.id === id)
  if (choices.defaultProfileId && allowed(choices.defaultProfileId)) return choices.defaultProfileId
  return allowed(preferred) ? preferred : ''
}
