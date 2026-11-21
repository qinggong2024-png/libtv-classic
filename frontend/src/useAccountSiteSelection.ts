import { useEffect, useState } from 'react'
import { getCutflowSiteChoices, type CutflowSiteChoices } from './api'
import { accountSiteSelection } from './accountSiteSelection'

export function useAccountSiteSelection(initialValue = '') {
  const [choices, setChoices] = useState<CutflowSiteChoices | null>(null)
  const [selected, setSelected] = useState(initialValue)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void getCutflowSiteChoices(controller.signal).then((result) => {
      if (!controller.signal.aborted) setChoices(result)
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '品牌读取失败')
    })
    return () => controller.abort()
  }, [])
  const siteProfileId = choices ? accountSiteSelection(choices, selected) : ''
  return { choices, error, siteProfileId, setSiteProfileId: setSelected, defaultProfileId: choices?.defaultProfileId ?? '' }
}
