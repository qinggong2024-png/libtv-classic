import type { AccountBrand } from '../../types'

/** De-duplicate brand selectors without changing persisted account/ledger UUIDs. */
export function selectableBrands(brands: AccountBrand[]): AccountBrand[] {
  const selections = new Map<string, AccountBrand>()
  for (const brand of brands) {
    const key = brand.siteProfileId || brand.id
    const current = selections.get(key)
    const preference = (item: AccountBrand) => {
      const name = item.name.toLocaleLowerCase()
      if (name === item.shortName?.toLocaleLowerCase()) return 2
      if (name === item.fullName?.toLocaleLowerCase()) return 1
      return 0
    }
    if (!current || preference(brand) > preference(current)) {
      selections.set(key, brand)
    }
  }
  return [...selections.values()].sort((left, right) =>
    (left.displayName ?? left.name).localeCompare(right.displayName ?? right.name, 'en')
  )
}

/** Allow an assigned legacy alias to stay selected during an unrelated policy edit. */
export function accountBrandOptions(brands: AccountBrand[], assignedId?: string | null): AccountBrand[] {
  const options = selectableBrands(brands)
  if (assignedId && !options.some((brand) => brand.id === assignedId)) {
    const historical = brands.find((brand) => brand.id === assignedId)
    if (historical) options.push(historical)
  }
  return options
}

export function equivalentBrandIds(brands: AccountBrand[], selectedId: string): string[] {
  const selected = brands.find((brand) => brand.id === selectedId)
  if (!selected?.siteProfileId) return [selectedId]
  return brands.filter((brand) => brand.siteProfileId === selected.siteProfileId)
    .map((brand) => brand.id)
}
